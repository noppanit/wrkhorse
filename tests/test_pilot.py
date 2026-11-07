"""Local tests. No paid APIs and no hosts other than 127.0.0.1."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
import tempfile
import threading
import unittest
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from wrkhorse.auth import RateLimiter, bearer_matches
from wrkhorse.config import (
    ConfigError,
    load_gateway_settings,
    load_worker_settings,
    validate_token,
)
from wrkhorse.diffutil import DiffError, apply_diff, parse_unified
from wrkhorse.executor import run_unittest
from wrkhorse.fixtures import load_sample
from wrkhorse.inference import InferenceError, chat_endpoint, request_diff
from wrkhorse.logsetup import ScrubFilter, configure_logging
from wrkhorse.sandbox import SandboxError, copy_tree
from wrkhorse.store import Store, public_result, stamp, utcnow
from wrkhorse.worker import Worker

REPO = Path(__file__).resolve().parents[1]
TOKEN = "a" * 32 + "b" * 8
INFERENCE_KEY = "k" * 24 + "inference-key"


class AuthTests(unittest.TestCase):
    def test_bearer_constant_time_match(self):
        self.assertTrue(bearer_matches(f"Bearer {TOKEN}", TOKEN))
        self.assertTrue(bearer_matches(f"bearer {TOKEN}", TOKEN))
        self.assertFalse(bearer_matches(f"Bearer {TOKEN[:-1]}x", TOKEN))
        self.assertFalse(bearer_matches(f"Bearer  {TOKEN}", TOKEN))
        self.assertFalse(bearer_matches(TOKEN, TOKEN))

    def test_token_policy(self):
        validate_token(TOKEN)
        with self.assertRaises(ConfigError):
            validate_token("short")
        with self.assertRaises(ConfigError):
            validate_token("replace-with-a-random-token-at-least-32-chars")

    def test_rate_limit(self):
        limiter = RateLimiter(2, 60)
        self.assertTrue(limiter.allow("client"))
        self.assertTrue(limiter.allow("client"))
        self.assertFalse(limiter.allow("client"))


class LogTests(unittest.TestCase):
    def test_scrub_filter_redacts_token(self):
        import logging

        configure_logging()
        previous = os.environ.get("WRKHORSE_AUTH_TOKEN")
        os.environ["WRKHORSE_AUTH_TOKEN"] = TOKEN
        try:
            record = logging.LogRecord("wrkhorse", logging.INFO, __file__, 1, "token %s", (TOKEN,), None)
            self.assertTrue(ScrubFilter().filter(record))
            self.assertNotIn(TOKEN, record.getMessage())
            self.assertIn("[redacted]", record.getMessage())
        finally:
            if previous is None:
                os.environ.pop("WRKHORSE_AUTH_TOKEN", None)
            else:
                os.environ["WRKHORSE_AUTH_TOKEN"] = previous

    def test_exception_traceback_does_not_keep_the_key(self):
        import io
        import logging

        previous = os.environ.get("WRKHORSE_INFERENCE_API_KEY")
        os.environ["WRKHORSE_INFERENCE_API_KEY"] = INFERENCE_KEY
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.addFilter(ScrubFilter())
        logger = logging.getLogger("wrkhorse.scrubtest")
        logger.handlers = [handler]
        logger.setLevel(logging.INFO)
        logger.propagate = False
        try:
            try:
                raise RuntimeError(f"endpoint key {INFERENCE_KEY}")
            except RuntimeError:
                logger.exception("inference failed")
            text = stream.getvalue()
        finally:
            if previous is None:
                os.environ.pop("WRKHORSE_INFERENCE_API_KEY", None)
            else:
                os.environ["WRKHORSE_INFERENCE_API_KEY"] = previous
        self.assertNotIn(INFERENCE_KEY, text)
        self.assertNotIn("Traceback", text)


class ConfigTests(unittest.TestCase):
    def test_gateway_does_not_require_inference_key(self):
        with _env(WRKHORSE_AUTH_TOKEN=TOKEN, WRKHORSE_INFERENCE_API_KEY=INFERENCE_KEY):
            settings = load_gateway_settings()
        self.assertFalse(hasattr(settings, "inference_api_key"))
        self.assertEqual(settings.host, "127.0.0.1")

    def test_worker_does_not_require_gateway_token(self):
        with _env(WRKHORSE_EXECUTION_MODE="disabled"):
            os.environ.pop("WRKHORSE_AUTH_TOKEN", None)
            settings = load_worker_settings()
        self.assertEqual(settings.execution_mode, "disabled")
        self.assertFalse(hasattr(settings, "token"))

    def test_public_bind_is_refused_without_opt_in(self):
        with _env(WRKHORSE_AUTH_TOKEN=TOKEN):
            with self.assertRaises(ConfigError):
                load_gateway_settings(host="0.0.0.0", port=8765)

    def test_serve_exits_when_token_missing(self):
        env = {"PATH": "/usr/bin:/bin", "HOME": "/tmp", "LANG": "C.UTF-8"}
        proc = subprocess.run(
            [sys.executable, "-m", "wrkhorse", "serve"],
            cwd=REPO,
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("WRKHORSE_AUTH_TOKEN", proc.stderr)
        self.assertNotIn(TOKEN, proc.stderr)


class DiffTests(unittest.TestCase):
    def test_reviewed_patch_fixes_fixture(self):
        _manifest, patch, repo = load_sample()
        parsed = parse_unified(patch, max_bytes=32768)
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw) / "repo"
            copy_tree(repo, workspace)
            before = run_unittest(workspace, timeout=10)
            self.assertFalse(before.passed)
            original = (workspace / "adder/mathy.py").read_text(encoding="utf-8")
            apply_diff(workspace, "adder/mathy.py", parsed, original)
            after = run_unittest(workspace, timeout=10)
        self.assertTrue(after.passed)
        self.assertIn("OK", after.output)
        self.assertIn("test_two_plus_three", after.output)

    def test_path_escape_and_symlink_are_refused(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            outside = root / "outside.txt"
            outside.write_text("safe", encoding="utf-8")
            workspace = root / "ws"
            (workspace / "adder").mkdir(parents=True)
            link = workspace / "adder" / "mathy.py"
            link.symlink_to(outside)
            malicious = (
                "--- a/adder/mathy.py\n"
                "+++ b/adder/mathy.py\n"
                "@@ -1 +1 @@\n"
                "-safe\n"
                "+pwned\n"
            )
            parsed = parse_unified(malicious, max_bytes=32768)
            with self.assertRaises(SandboxError):
                apply_diff(workspace, "adder/mathy.py", parsed, "safe\n")
            self.assertEqual(outside.read_text(encoding="utf-8"), "safe")
        with self.assertRaises(DiffError):
            parse_unified(
                "--- a/../../etc/passwd\n+++ b/../../etc/passwd\n@@ -1 +1 @@\n-x\n+y\n",
                max_bytes=32768,
            )

    def test_shell_text_is_not_a_diff(self):
        with self.assertRaises(DiffError):
            parse_unified("os.system('touch /tmp/wrkhorse-pwned')\n", max_bytes=32768)

    def test_child_environment_drops_parent_secrets(self):
        sentinel = "SENTINEL-" + ("s" * 24)
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw) / "repo"
            (workspace / "tests").mkdir(parents=True)
            (workspace / "tests" / "__init__.py").write_text("", encoding="utf-8")
            (workspace / "tests" / "test_env.py").write_text(
                "import os, unittest\n"
                "class EnvTests(unittest.TestCase):\n"
                "    def test_absent(self):\n"
                "        open('seen.txt','w').write(os.environ.get('SENTINEL_SECRET','ABSENT'))\n",
                encoding="utf-8",
            )
            previous = os.environ.get("SENTINEL_SECRET")
            os.environ["SENTINEL_SECRET"] = sentinel
            try:
                run_unittest(workspace, timeout=10)
            finally:
                if previous is None:
                    os.environ.pop("SENTINEL_SECRET", None)
                else:
                    os.environ["SENTINEL_SECRET"] = previous
            seen = (workspace / "seen.txt").read_text(encoding="utf-8")
        self.assertEqual(seen, "ABSENT")
        self.assertNotIn(sentinel, seen)


class StoreTests(unittest.TestCase):
    def test_claim_does_not_steal_a_fresh_running_job(self):
        with tempfile.TemporaryDirectory() as raw:
            store = Store(Path(raw) / "q.sqlite", stale_seconds=900, max_attempts=2)
            job = store.enqueue("sample-add", "mock")
            first = store.claim("worker-a")
            second = store.claim("worker-b")
            self.assertIsNotNone(first)
            self.assertEqual(first["job_id"], job["job_id"])
            self.assertIsNone(second)
            self.assertEqual(store.get(job["job_id"])["claimed_by"], "worker-a")
            self.assertEqual(store.get(job["job_id"])["attempts"], 1)

    def test_stale_claim_retries_then_stops(self):
        with tempfile.TemporaryDirectory() as raw:
            store = Store(Path(raw) / "q.sqlite", stale_seconds=30, max_attempts=2)
            job = store.enqueue("sample-add", "mock")
            self.assertIsNotNone(store.claim("worker-a"))
            _age(store, job["job_id"])
            second = store.claim("worker-b")
            self.assertEqual(second["claimed_by"], "worker-b")
            self.assertEqual(second["attempts"], 2)
            _age(store, job["job_id"])
            self.assertIsNone(store.claim("worker-c"))
            self.assertEqual(store.get(job["job_id"])["status"], "failed")
            self.assertEqual(store.get(job["job_id"])["error"], "attempts_exhausted")

    def test_two_claimers_race(self):
        with tempfile.TemporaryDirectory() as raw:
            store = Store(Path(raw) / "q.sqlite", stale_seconds=900, max_attempts=2)
            store.enqueue("sample-add", "mock")
            barrier = threading.Barrier(2)
            found = []

            def race(name: str) -> None:
                barrier.wait()
                found.append(store.claim(name))

            threads = [threading.Thread(target=race, args=(name,)) for name in ("a", "b")]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            claimed = [job for job in found if job is not None]
            self.assertEqual(len(claimed), 1)

    def test_unknown_task_is_not_stored(self):
        with tempfile.TemporaryDirectory() as raw:
            store = Store(Path(raw) / "q.sqlite", stale_seconds=30, max_attempts=2)
            with self.assertRaises(ValueError):
                store.enqueue("../../etc/passwd", "mock")
            with self.connect_count(store) as count:
                self.assertEqual(count, 0)

    @staticmethod
    def connect_count(store: Store):
        from contextlib import contextmanager

        @contextmanager
        def _count():
            with store.connect() as con:
                count = con.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"]
            yield count

        return _count()


class WorkerPolicyTests(unittest.TestCase):
    def test_default_mode_refuses_without_running_tests_or_calling_model(self):
        calls = {"tests": 0, "model": 0}

        def tests(*_args, **_kwargs):
            calls["tests"] += 1
            raise AssertionError("tests ran")

        def model(*_args, **_kwargs):
            calls["model"] += 1
            raise AssertionError("model called")

        with tempfile.TemporaryDirectory() as raw:
            settings = _worker_settings(Path(raw), "disabled")
            settings = _with_inference(settings, "http://127.0.0.1:9/v1")
            store = Store(settings.db_path, stale_seconds=30, max_attempts=2)
            store.enqueue("sample-add", "inference")
            with _patch("wrkhorse.worker.run_unittest", tests), _patch(
                "wrkhorse.worker.request_diff", model
            ):
                Worker(settings, store, "worker-test").once()
            job = _only(store)
        self.assertEqual(job["status"], "refused")
        self.assertEqual(job["error"], "execution_disabled")
        self.assertEqual(calls, {"tests": 0, "model": 0})
        result = public_result(job)["result"]
        self.assertFalse(result["tests_ran"])
        self.assertFalse(result["inference_called"])

    def test_mock_demo_mode_runs_reviewed_test_only(self):
        with tempfile.TemporaryDirectory() as raw:
            settings = _worker_settings(Path(raw), "trusted-local-demo")
            store = Store(settings.db_path, stale_seconds=30, max_attempts=2)
            store.enqueue("sample-add", "mock")
            Worker(settings, store, "worker-test").once()
            job = _only(store)
            result = public_result(job)["result"]
            self.assertEqual(job["status"], "succeeded")
            self.assertTrue(result["tests_passed"])
            self.assertIn("+    return left + right", result["diff"])
            self.assertFalse(result["inference_called"])
            artifact = Path(raw) / "artifacts" / job["job_id"] / "attempt-1" / "diff.patch"
            self.assertTrue(artifact.is_file())

    def test_inference_saves_diff_and_does_not_execute_it(self):
        canary = None
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            canary = root / "canary"
            settings = _with_inference(_worker_settings(root / "db", "trusted-local-demo"), None)
            store = Store(settings.db_path, stale_seconds=30, max_attempts=2)
            store.enqueue("sample-add", "inference")
            hits = {"n": 0}

            def fake_request(**_kwargs):
                hits["n"] += 1
                return (
                    "--- a/adder/mathy.py\n"
                    "+++ b/adder/mathy.py\n"
                    "@@ -1,2 +1,3 @@\n"
                    " def add(left, right):\n"
                    "-    return left - right\n"
                    "+    import os\n"
                    f"+    os.system('touch {canary}')\n"
                )

            with _patch("wrkhorse.worker.request_diff", fake_request):
                settings = _with_inference(settings, "http://127.0.0.1:9/v1")
                Worker(settings, store, "worker-test").once()
            job = _only(store)
            self.assertFalse(canary.exists())
        self.assertEqual(hits["n"], 1)
        self.assertEqual(job["status"], "diff_ready")
        result = public_result(job)["result"]
        self.assertFalse(result["tests_ran"])
        self.assertTrue(result["inference_called"])
        self.assertFalse(result["executes_model_generated_code"])

    def test_inference_without_config_does_not_open_a_socket(self):
        def explode(**_kwargs):
            raise AssertionError("socket opened")

        with tempfile.TemporaryDirectory() as raw:
            settings = _worker_settings(Path(raw), "trusted-local-demo")
            store = Store(settings.db_path, stale_seconds=30, max_attempts=2)
            store.enqueue("sample-add", "inference")
            with _patch("wrkhorse.worker.request_diff", explode):
                Worker(settings, store, "worker-test").once()
            job = _only(store)
        self.assertEqual(job["error"], "inference_not_configured")


class InferenceClientTests(unittest.TestCase):
    def test_local_stub_and_refused_redirect(self):
        hits = {"chat": 0, "secret": 0}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                if self.path == "/v1/chat/completions":
                    hits["chat"] += 1
                    auth = self.headers.get("Authorization", "")
                    if auth != f"Bearer {INFERENCE_KEY}" or TOKEN in auth:
                        self.send_response(401)
                        self.end_headers()
                        return
                    body = json.dumps(
                        {"choices": [{"message": {"content": "--- a/x\n+++ b/x\n"}}]}
                    ).encode()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(404)
                self.end_headers()

            def do_GET(self):  # noqa: N802
                hits["secret"] += 1
                self.send_response(200)
                self.end_headers()

            def log_message(self, _format, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            content = request_diff(
                base_url=f"http://127.0.0.1:{port}/v1",
                api_key=INFERENCE_KEY,
                model="fixture-model",
                prompt="fix add",
                timeout=5,
            )
            self.assertIn("--- a/x", content)
            redirect = ThreadingHTTPServer(("127.0.0.1", 0), _Redirect)
            redirect_port = redirect.server_address[1]
            redirect_thread = threading.Thread(target=redirect.serve_forever, daemon=True)
            redirect_thread.start()
            try:
                with self.assertRaises(InferenceError):
                    request_diff(
                        base_url=f"http://127.0.0.1:{redirect_port}/v1",
                        api_key=INFERENCE_KEY,
                        model="fixture-model",
                        prompt="fix add",
                        timeout=5,
                    )
            finally:
                redirect.shutdown()
                redirect.server_close()
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(hits["chat"], 1)
        self.assertEqual(hits["secret"], 0)
        with self.assertRaises(InferenceError):
            chat_endpoint("file:///tmp/model")
        with self.assertRaises(InferenceError):
            chat_endpoint("http://user:secret@127.0.0.1/v1")

    def test_fast_drip_body_cannot_extend_the_deadline(self):
        self._assert_deadline(self._FastBody)

    def test_slow_headers_cannot_extend_the_deadline(self):
        self._assert_deadline(self._SlowHeaders)

    def test_fragmented_response_completes_within_the_deadline(self):
        content = self._serve_and_call(self._Fragmented, timeout=3.0)
        self.assertIn("--- a/adder/mathy.py", content)

    def _assert_deadline(self, handler):
        started = time.monotonic()
        with self.assertRaises(InferenceError) as caught:
            self._serve_and_call(handler, timeout=1.0)
        self.assertEqual(str(caught.exception), "inference deadline exceeded")
        self.assertLess(time.monotonic() - started, 2.5)

    def _serve_and_call(self, handler, timeout: float) -> str:
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            return request_diff(
                base_url=f"http://127.0.0.1:{port}/v1",
                api_key=INFERENCE_KEY,
                model="fixture-model",
                prompt="fix add",
                timeout=timeout,
            )
        finally:
            server.shutdown()
            server.server_close()

    class _FastBody(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            _read_body(self)
            self.send_response(200)
            self.send_header("Content-Length", "5000")
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                for _ in range(5000):
                    time.sleep(0.1)
                    self.wfile.write(b"x")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
                return

        def log_message(self, _format, *_args):
            return

    class _SlowHeaders(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            _read_body(self)
            raw = (
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                b"Content-Length: 2\r\nConnection: close\r\n\r\n{}"
            )
            try:
                for index in range(len(raw)):
                    time.sleep(0.1)
                    self.wfile.write(raw[index : index + 1])
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
                return

        def log_message(self, _format, *_args):
            return

    class _Fragmented(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            _read_body(self)
            payload = json.dumps(
                {"choices": [{"message": {"content": "--- a/adder/mathy.py\n+++ b/adder/mathy.py\n"}}]}
            ).encode()
            raw = (
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                + f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
                + payload
            )
            try:
                for index in range(0, len(raw), 7):
                    self.wfile.write(raw[index : index + 7])
                    self.wfile.flush()
                    time.sleep(0.01)
            except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
                return

        def log_message(self, _format, *_args):
            return


def _read_body(handler: BaseHTTPRequestHandler) -> None:
    length = int(handler.headers.get("Content-Length", "0"))
    if length:
        handler.rfile.read(length)


class RedirectHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        self.send_response(302)
        self.send_header("Location", "http://127.0.0.1/secret")
        self.end_headers()

    def log_message(self, _format, *_args):
        return


_Redirect = RedirectHandler


class GatewayTests(unittest.TestCase):
    def test_streamable_http_auth_and_tools(self):
        import asyncio

        import httpx2
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        from wrkhorse.server import http_app

        with tempfile.TemporaryDirectory() as raw:
            db = Path(raw) / "q.sqlite"
            with _env(WRKHORSE_AUTH_TOKEN=TOKEN, WRKHORSE_DB=str(db)):
                settings = load_gateway_settings(host="127.0.0.1", port=0)
            app = http_app(settings)
            import uvicorn

            config = uvicorn.Config(app, host="127.0.0.1", port=0, access_log=False, log_level="warning")
            server = uvicorn.Server(config)
            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()
            _wait_until(lambda: server.started)
            sockets = [sock for server_obj in server.servers for sock in server_obj.sockets]
            port = sockets[0].getsockname()[1]
            try:
                with self.assertRaises(HTTPError) as denied:
                    urlopen(
                        Request(f"http://127.0.0.1:{port}/mcp", method="POST", data=b"{}"),
                        timeout=5,
                    )
                self.assertEqual(denied.exception.code, 401)

                async def call():
                    async with httpx2.AsyncClient(
                        headers={"Authorization": f"Bearer {TOKEN}"},
                        timeout=10,
                    ) as http:
                        async with streamable_http_client(
                            f"http://127.0.0.1:{port}/mcp",
                            http_client=http,
                        ) as (read, write):
                            async with ClientSession(read, write) as session:
                                await session.initialize()
                                tools = await session.list_tools()
                                names = sorted(tool.name for tool in tools.tools)
                                submitted = json.loads(
                                    (
                                        await session.call_tool(
                                            "submit_task",
                                            {"task_name": "sample-add", "mode": "mock"},
                                        )
                                    ).content[0].text
                                )
                                missing = await session.call_tool(
                                    "submit_task",
                                    {"task_name": "../secret", "mode": "mock"},
                                )
                                return names, submitted, missing

                names, submitted, missing = asyncio.run(call())
            finally:
                server.should_exit = True
                thread.join(timeout=5)
            self.assertEqual(
                names,
                [
                    "get_project_status",
                    "get_task_result",
                    "get_task_status",
                    "submit_task",
                ],
            )
            self.assertEqual(submitted["status"], "queued")
            self.assertTrue(missing.is_error)
            text = missing.content[0].text
            self.assertNotIn("../secret", text)
            store = Store(db, stale_seconds=30, max_attempts=2)
            self.assertEqual(store.get(submitted["job_id"])["status"], "queued")


class DemoTests(unittest.TestCase):
    def test_documented_demo(self):
        from wrkhorse.demo import run_demo

        text = run_demo()
        self.assertIn("status: succeeded", text)
        self.assertIn("+    return left + right", text)
        self.assertIn("OK", text)
        self.assertIn("executes_model_generated_code: false", text)
        self.assertNotIn(TOKEN, text)


class LeaseTests(unittest.TestCase):
    def test_stale_attempt_cannot_finish_newer_claim(self):
        with tempfile.TemporaryDirectory() as raw:
            store = Store(Path(raw) / "q.sqlite", stale_seconds=30, max_attempts=2)
            job = store.enqueue("sample-add", "mock")
            first = store.claim("worker-a")
            _age(store, job["job_id"])
            second = store.claim("worker-b")
            self.assertFalse(
                store.finish(
                    job["job_id"],
                    "worker-a",
                    first["attempts"],
                    status="succeeded",
                    error=None,
                    result={"code": "stale"},
                )
            )
            current = store.get(job["job_id"])
            self.assertEqual(current["claimed_by"], "worker-b")
            self.assertEqual(current["attempts"], second["attempts"])
            self.assertEqual(current["status"], "running")
            self.assertIsNone(current["result_json"])
            self.assertTrue(
                store.finish(
                    job["job_id"],
                    "worker-b",
                    second["attempts"],
                    status="succeeded",
                    error=None,
                    result={"code": "tests_passed"},
                )
            )
            self.assertFalse(
                store.touch(job["job_id"], "worker-a", first["attempts"])
            )
            saved = json.loads(store.get(job["job_id"])["result_json"])
        self.assertEqual(saved["code"], "tests_passed")

    def test_running_worker_process_recovers_heartbeat_after_long_lock(self):
        patch = load_sample()[1]
        with tempfile.TemporaryDirectory() as raw:
            db = Path(raw) / "q.sqlite"
            release = threading.Event()
            started = threading.Event()
            posts = {"n": 0}

            class Hold(BaseHTTPRequestHandler):
                def do_POST(self):  # noqa: N802
                    _read_body(self)
                    posts["n"] += 1
                    started.set()
                    release.wait(30)
                    _send_json(self, patch)

                def log_message(self, _format, *_args):
                    return

            server = ThreadingHTTPServer(("127.0.0.1", 0), Hold)
            port = server.server_address[1]
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            env = _process_env(
                db,
                WRKHORSE_EXECUTION_MODE="trusted-local-demo",
                WRKHORSE_INFERENCE_BASE_URL=f"http://127.0.0.1:{port}/v1",
                WRKHORSE_INFERENCE_API_KEY=INFERENCE_KEY,
                WRKHORSE_INFERENCE_MODEL="fixture-model",
                WRKHORSE_INFERENCE_TIMEOUT_SECONDS="30",
                WRKHORSE_CLAIM_STALE_SECONDS="30",
            )
            proc = _spawn_worker(env)
            store = Store(db, stale_seconds=30, max_attempts=2)
            try:
                store.enqueue("sample-add", "inference")
                self.assertTrue(started.wait(10), "inference request did not start")
                self.assertIsNone(proc.poll())
                with store.connect() as con:
                    seen_before = con.execute("SELECT claimed_at FROM tasks").fetchone()["claimed_at"]
                _hold_write_lock(db, 6)
                self.assertIsNone(proc.poll())
                recovered = False
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    self.assertIsNone(proc.poll())
                    with store.connect() as con:
                        seen_after = con.execute(
                            "SELECT claimed_at FROM tasks"
                        ).fetchone()["claimed_at"]
                    if seen_after > seen_before:
                        recovered = True
                        break
                    time.sleep(0.1)
                self.assertTrue(recovered, "heartbeat did not resume after the lock")
                release.set()
                job = _wait_terminal(store, 15)
                self.assertEqual(job["status"], "diff_ready")
                self.assertEqual(job["attempts"], 1)
                self.assertEqual(posts["n"], 1)
                recorded = json.loads(job["result_json"])["artifact_dir"]
                self.assertEqual(recorded, f"artifacts/{job['job_id']}/attempt-1")
                winner = Path(raw) / recorded / "diff.patch"
                self.assertEqual(winner.read_text(encoding="utf-8"), patch)
                self.assertFalse((Path(raw) / "artifacts" / job["job_id"] / "diff.patch").exists())
            finally:
                release.set()
                _stop_process(proc)
                server.shutdown()
                server.server_close()

    def test_separate_processes_reclaim_without_overwriting_winner_artifacts(self):
        import signal

        patch = load_sample()[1]
        with tempfile.TemporaryDirectory() as raw:
            db = Path(raw) / "q.sqlite"
            release_first = threading.Event()
            first_started = threading.Event()

            class Gate(BaseHTTPRequestHandler):
                def do_POST(self):  # noqa: N802
                    _read_body(self)
                    if not first_started.is_set():
                        first_started.set()
                        release_first.wait(30)
                    _send_json(self, patch)

                def log_message(self, _format, *_args):
                    return

            server = ThreadingHTTPServer(("127.0.0.1", 0), Gate)
            port = server.server_address[1]
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            env = _process_env(
                db,
                WRKHORSE_EXECUTION_MODE="trusted-local-demo",
                WRKHORSE_INFERENCE_BASE_URL=f"http://127.0.0.1:{port}/v1",
                WRKHORSE_INFERENCE_API_KEY=INFERENCE_KEY,
                WRKHORSE_INFERENCE_MODEL="fixture-model",
                WRKHORSE_INFERENCE_TIMEOUT_SECONDS="30",
                WRKHORSE_CLAIM_STALE_SECONDS="2",
                WRKHORSE_MAX_ATTEMPTS="2",
            )
            first = _spawn_worker(env)
            store = Store(db, stale_seconds=2, max_attempts=2)
            second = None
            try:
                store.enqueue("sample-add", "inference")
                self.assertTrue(first_started.wait(10), "first worker did not call the model")
                self.assertIsNone(first.poll())
                os.kill(first.pid, signal.SIGSTOP)
                time.sleep(3)
                second = _spawn_worker(env)
                job = _wait_terminal(store, 15)
                self.assertEqual(job["claimed_by"], f"worker-{second.pid}")
                self.assertEqual(job["attempts"], 2)
                self.assertEqual(job["status"], "diff_ready")
                recorded = json.loads(job["result_json"])["artifact_dir"]
                self.assertEqual(recorded, f"artifacts/{job['job_id']}/attempt-2")
                winner = Path(raw) / recorded / "diff.patch"
                winner_bytes = winner.read_bytes()
                self.assertFalse(
                    store.finish(
                        job["job_id"],
                        f"worker-{first.pid}",
                        1,
                        status="succeeded",
                        error=None,
                        result={"code": "stale", "artifact_dir": f"artifacts/{job['job_id']}/attempt-1"},
                    )
                )
                self.assertFalse(store.touch(job["job_id"], f"worker-{first.pid}", 1))
                release_first.set()
                os.kill(first.pid, signal.SIGCONT)
                time.sleep(2)
                self.assertEqual(winner.read_bytes(), winner_bytes)
                self.assertEqual(store.get(job["job_id"])["result_json"], job["result_json"])
                stale_file = Path(raw) / "artifacts" / job["job_id"] / "attempt-1" / "diff.patch"
                if stale_file.exists():
                    self.assertNotEqual(stale_file.resolve(), winner.resolve())
                self.assertFalse((Path(raw) / "artifacts" / job["job_id"] / "diff.patch").exists())
            finally:
                release_first.set()
                if first.poll() is None:
                    try:
                        os.kill(first.pid, signal.SIGCONT)
                    except ProcessLookupError:
                        pass
                _stop_process(first)
                if second is not None:
                    _stop_process(second)
                server.shutdown()
                server.server_close()

    def test_separate_worker_process_survives_six_second_lock(self):
        with tempfile.TemporaryDirectory() as raw:
            db = Path(raw) / "q.sqlite"
            env = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": raw,
                "LANG": "C.UTF-8",
                "WRKHORSE_DB": str(db),
                "WRKHORSE_EXECUTION_MODE": "disabled",
                "WRKHORSE_POLL_SECONDS": "0.05",
                "PYTHONUNBUFFERED": "1",
            }
            proc = subprocess.Popen(
                [sys.executable, "-m", "wrkhorse", "worker"],
                cwd=REPO,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            store = Store(db, stale_seconds=30, max_attempts=2)
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if proc.poll() is not None:
                        self.fail(proc.stdout.read() if proc.stdout else "worker exited")
                    if store.project_status()["worker_connected"]:
                        break
                    time.sleep(0.05)
                else:
                    self.fail("worker did not heartbeat")
                with store.connect() as con:
                    seen_before = con.execute("SELECT last_seen FROM heartbeats").fetchone()["last_seen"]
                con = sqlite3.connect(db, timeout=1, isolation_level=None)
                con.execute("BEGIN IMMEDIATE")
                time.sleep(6)
                self.assertIsNone(proc.poll())
                con.execute("COMMIT")
                con.close()
                recovered = False
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    self.assertIsNone(proc.poll())
                    with store.connect() as check:
                        seen_after = check.execute("SELECT last_seen FROM heartbeats").fetchone()["last_seen"]
                    if seen_after > seen_before:
                        recovered = True
                        break
                    time.sleep(0.2)
                self.assertTrue(recovered)
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                if proc.stdout is not None:
                    proc.stdout.close()


def _process_env(db: Path, **extra: str) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(db.parent),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONUNBUFFERED": "1",
        "WRKHORSE_DB": str(db),
        "WRKHORSE_POLL_SECONDS": "0.05",
    }
    library = os.environ.get("LD_LIBRARY_PATH")
    if library:
        env["LD_LIBRARY_PATH"] = library
    env.update(extra)
    return env


def _spawn_worker(env: dict[str, str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-m", "wrkhorse", "worker"],
        cwd=REPO,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _stop_process(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    if proc.stdout is not None:
        proc.stdout.close()


def _output(proc: subprocess.Popen[str]) -> str:
    if proc.stdout is None:
        return ""
    return proc.stdout.read()


def _wait_terminal(store: Store, seconds: float) -> dict:
    deadline = time.monotonic() + seconds
    job = None
    while time.monotonic() < deadline:
        job = _only_or_none(store)
        if job is not None and job["status"] in {"succeeded", "diff_ready", "failed", "refused"}:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job did not finish: {job}")


def _send_json(handler: BaseHTTPRequestHandler, content: str) -> None:
    raw = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
    try:
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(raw)))
        handler.end_headers()
        handler.wfile.write(raw)
    except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
        return


def _hold_write_lock(db: Path, seconds: float) -> None:
    """Hold the write lock without resetting the wait each time another writer commits."""
    con = sqlite3.connect(db, timeout=0, isolation_level=None)
    deadline = time.monotonic() + 3
    while True:
        try:
            con.execute("BEGIN IMMEDIATE")
            break
        except sqlite3.OperationalError:
            if time.monotonic() > deadline:
                con.close()
                raise AssertionError("could not acquire the database write lock")
            time.sleep(0.05)
    try:
        time.sleep(seconds)
    finally:
        con.execute("COMMIT")
        con.close()


def _only_or_none(store: Store) -> dict | None:
    with store.connect() as con:
        rows = con.execute("SELECT * FROM tasks").fetchall()
    if not rows:
        return None
    return dict(rows[0])


class EnvContext:
    def __init__(self, **values: str) -> None:
        self.values = values
        self.previous: dict[str, str | None] = {}

    def __enter__(self):
        for key, value in self.values.items():
            self.previous[key] = os.environ.get(key)
            os.environ[key] = value
        return self

    def __exit__(self, *_args):
        for key, value in self.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _env(**values: str) -> EnvContext:
    return EnvContext(**values)


def _age(store: Store, job_id: str) -> None:
    old = stamp(utcnow() - timedelta(seconds=120))
    with store.connect() as con:
        con.execute("UPDATE tasks SET claimed_at = ? WHERE job_id = ?", (old, job_id))


def _only(store: Store) -> dict:
    with store.connect() as con:
        rows = con.execute("SELECT * FROM tasks").fetchall()
    assert len(rows) == 1
    return dict(rows[0])


def _worker_settings(root: Path, mode: str):
    from wrkhorse.config import WorkerSettings

    root.mkdir(parents=True, exist_ok=True)
    return WorkerSettings(
        db_path=root / "wrkhorse.sqlite",
        execution_mode=mode,
        stale_seconds=30,
        max_attempts=2,
        poll_seconds=0.05,
        test_timeout_seconds=10,
        max_diff_bytes=32768,
        inference_base_url=None,
        inference_api_key=None,
        inference_model=None,
        inference_timeout_seconds=5,
    )


def _with_inference(settings, base_url: str | None):
    from dataclasses import replace

    return replace(
        settings,
        inference_base_url=base_url,
        inference_api_key=INFERENCE_KEY if base_url else None,
        inference_model="fixture-model" if base_url else None,
    )


class _patch:
    def __init__(self, target: str, value) -> None:
        self.target = target
        self.value = value
        self.saved = None

    def __enter__(self):
        module_name, attr = self.target.rsplit(".", 1)
        module = __import__(module_name, fromlist=[attr])
        self.module = module
        self.attr = attr
        self.saved = getattr(module, attr)
        setattr(module, attr, self.value)

    def __exit__(self, *_args):
        setattr(self.module, self.attr, self.saved)


def _wait_until(predicate, timeout: float = 5) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise TimeoutError("condition not met")


if __name__ == "__main__":
    unittest.main()
