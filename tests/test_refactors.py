"""Checks for the small refactors: shared cloud-tag policy, typed inference
errors, job status enum and rate-limiter cleanup."""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from wrkhorse import home_bridge
from wrkhorse.auth import RateLimiter
from wrkhorse.doctor import run_doctor
from wrkhorse.inference import (
    InferenceEmptyResponse,
    InferenceError,
    InferenceNotConfigured,
    InferenceTimeout,
    _parse_content,
    request_diff,
)
from wrkhorse.store import TERMINAL, JobStatus, Store


class HomeBridgeModelTagTests(unittest.TestCase):
    def test_dash_cloud_tags_are_refused_before_any_request(self):
        for tag in ("gpt-oss:120b-cloud", "qwen3-coder:cloud"):
            with self.subTest(tag=tag), patch.object(
                home_bridge, "request_local_model", side_effect=AssertionError("model was called")
            ), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as ctx:
                home_bridge.main(["--check", "--model", tag])
            self.assertEqual(ctx.exception.code, 2)


class InferenceErrorTypeTests(unittest.TestCase):
    def test_subclasses_still_count_as_inference_errors(self):
        for cls in (InferenceNotConfigured, InferenceTimeout, InferenceEmptyResponse):
            self.assertTrue(issubclass(cls, InferenceError))

    def test_each_failure_raises_its_own_type(self):
        with self.assertRaises(InferenceNotConfigured):
            request_diff(base_url="http://127.0.0.1:9/v1", api_key="", model="m", prompt="p", timeout=1)
        with self.assertRaises(InferenceTimeout):
            request_diff(base_url="http://127.0.0.1:9/v1", api_key="k", model="m", prompt="p", timeout=0)
        empty = json.dumps({"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]})
        with self.assertRaises(InferenceEmptyResponse):
            _parse_content(empty.encode())


class _Models(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"data": [{"id": "qwen2.5-coder:1.5b"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class DoctorWarmTests(unittest.TestCase):
    def test_only_an_empty_reply_counts_as_a_successful_warm_up(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Models)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        env = {
            "WRKHORSE_INFERENCE_BASE_URL": f"http://127.0.0.1:{server.server_address[1]}/v1",
            "WRKHORSE_INFERENCE_MODEL": "qwen2.5-coder:1.5b",
        }
        try:
            for error, expected in (
                (None, 0),
                (InferenceEmptyResponse("inference response was empty"), 0),
                (InferenceTimeout("inference deadline exceeded"), 2),
                (InferenceError("inference request failed"), 2),
                # Used to pass because the message matched neither keyword.
                (InferenceError("inference response was not a chat completion"), 2),
            ):
                lines: list[str] = []
                side = error if error is not None else (lambda **_kw: "ready")
                with self.subTest(error=error), patch.dict(os.environ, env, clear=True), patch(
                    "wrkhorse.doctor.request_diff", side_effect=side
                ):
                    self.assertEqual(run_doctor(warm=True, out=lines.append), expected, lines)
        finally:
            server.shutdown()
            server.server_close()


class JobStatusTests(unittest.TestCase):
    def test_terminal_set_and_values(self):
        self.assertEqual(
            {str(s) for s in TERMINAL}, {"succeeded", "diff_ready", "failed", "refused"}
        )
        self.assertNotIn(JobStatus.QUEUED, TERMINAL)
        self.assertNotIn(JobStatus.RUNNING, TERMINAL)

    def test_store_writes_plain_status_strings(self):
        with tempfile.TemporaryDirectory() as raw:
            store = Store(Path(raw) / "q.sqlite", stale_seconds=30, max_attempts=2)
            job = store.enqueue("sample-add", "mock")
            self.assertEqual(job["status"], "queued")
            claimed = store.claim("w1")
            self.assertEqual(claimed["status"], "running")
            with self.assertRaises(ValueError):
                store.finish(job["job_id"], "w1", 1, status=JobStatus.RUNNING, error=None, result={})
            self.assertTrue(
                store.finish(job["job_id"], "w1", 1, status=JobStatus.DIFF_READY, error=None, result={})
            )
            saved = store.get(job["job_id"])
            self.assertEqual(saved["status"], "diff_ready")
            self.assertIs(type(saved["status"]), str)


class RateLimiterTests(unittest.TestCase):
    def test_idle_clients_are_forgotten_after_a_window(self):
        clock = [1000.0]
        with patch("wrkhorse.auth.time.monotonic", side_effect=lambda: clock[0]):
            limiter = RateLimiter(2, 60)
            for n in range(50):
                self.assertTrue(limiter.allow(f"10.0.0.{n}"))
            self.assertEqual(len(limiter._hits), 50)
            clock[0] += 61
            self.assertTrue(limiter.allow("10.0.0.200"))
            self.assertEqual(list(limiter._hits), ["10.0.0.200"])

    def test_limit_still_applies_within_the_window(self):
        clock = [0.0]
        with patch("wrkhorse.auth.time.monotonic", side_effect=lambda: clock[0]):
            limiter = RateLimiter(2, 60)
            self.assertTrue(limiter.allow("a"))
            self.assertTrue(limiter.allow("a"))
            self.assertFalse(limiter.allow("a"))
            clock[0] += 61
            self.assertTrue(limiter.allow("a"))


if __name__ == "__main__":
    unittest.main()
