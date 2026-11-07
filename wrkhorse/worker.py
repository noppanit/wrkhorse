"""Long-running queue consumer. It is the only process that may call a model."""

from __future__ import annotations

import os
import re
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

from wrkhorse.config import WorkerSettings
from wrkhorse.diffutil import DiffError, apply_diff, parse_unified
from wrkhorse.executor import run_unittest
from wrkhorse.fixtures import load_sample, prompt_for
from wrkhorse.inference import InferenceError, request_diff
from wrkhorse.logsetup import get_logger
from wrkhorse.sandbox import SandboxError, copy_tree
from wrkhorse.store import Store, is_busy

log = get_logger(__name__)
_JOB_ID = re.compile(r"^[0-9a-f]{32}$")
_ARTIFACTS = {"diff.patch", "test.txt", "result.json"}


def _write_artifact(root: Path, job_id: str, name: str, text: str) -> None:
    if _JOB_ID.fullmatch(job_id) is None or name not in _ARTIFACTS:
        raise RuntimeError("refusing artifact path")
    dest_dir = root / job_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / name
    resolved_root = root.resolve()
    if resolved_root not in target.resolve().parents:
        raise RuntimeError("artifact path escapes")
    target.write_text(text, encoding="utf-8")


def _result(
    *,
    code: str,
    diff: str | None,
    tests_ran: bool,
    tests_passed: bool | None,
    test_output: str | None,
    inference_called: bool,
    job_id: str,
) -> dict:
    return {
        "code": code,
        "diff": diff,
        "tests_ran": tests_ran,
        "tests_passed": tests_passed,
        "test_output": test_output,
        "inference_called": inference_called,
        "executes_model_generated_code": False,
        "subprocess_is_sandbox": False,
        "artifact_dir": f"artifacts/{job_id}" if diff or test_output else None,
    }


class Worker:
    def __init__(self, settings: WorkerSettings, store: Store, worker_id: str) -> None:
        self.settings = settings
        self.store = store
        self.worker_id = worker_id

    def once(self) -> bool:
        self.store.heartbeat(self.settings.execution_mode)
        job = self.store.claim(self.worker_id)
        if job is None:
            return False
        self._run(job)
        return True

    def serve_forever(self) -> None:
        log.info("worker started execution_mode=%s", self.settings.execution_mode)
        while True:
            try:
                self.once()
            except sqlite3.OperationalError as exc:
                if not is_busy(exc):
                    raise
                log.warning("queue lock busy; worker stays up and will retry")
            time.sleep(self.settings.poll_seconds)

    def _run(self, job: dict) -> None:
        attempts = int(job["attempts"])
        stop = threading.Event()
        lease_lost = threading.Event()

        def beat() -> None:
            interval = min(2.0, max(0.2, self.settings.stale_seconds / 3))
            while not stop.wait(interval):
                try:
                    if not self.store.touch(job["job_id"], self.worker_id, attempts):
                        lease_lost.set()
                        return
                    self.store.heartbeat(self.settings.execution_mode)
                except sqlite3.OperationalError as exc:
                    if not is_busy(exc):
                        lease_lost.set()
                        return
                    log.warning("heartbeat deferred because the queue lock was busy")

        thread = threading.Thread(target=beat, name="wrkhorse-heartbeat", daemon=True)
        thread.start()
        try:
            try:
                status, error, result = self._process(job, lease_lost)
            except Exception as exc:
                log.warning(
                    "job %s failed inside the worker (%s)",
                    job["job_id"],
                    type(exc).__name__,
                )
                status, error, result = (
                    "failed",
                    "worker_error",
                    _result(
                        code="worker_error",
                        diff=None,
                        tests_ran=False,
                        tests_passed=None,
                        test_output=None,
                        inference_called=False,
                        job_id=job["job_id"],
                    ),
                )
            if lease_lost.is_set():
                log.warning("lost claim for job %s", job["job_id"])
                return
            wrote = self.store.finish(
                job["job_id"],
                self.worker_id,
                attempts,
                status=status,
                error=error,
                result=result,
            )
            if not wrote:
                log.warning("lost claim for job %s", job["job_id"])
        finally:
            stop.set()
            thread.join(timeout=1)

    def _process(self, job: dict, lease_lost: threading.Event) -> tuple[str, str | None, dict]:
        job_id = job["job_id"]
        if self.settings.execution_mode != "trusted-local-demo":
            return (
                "refused",
                "execution_disabled",
                _result(
                    code="execution_disabled",
                    diff=None,
                    tests_ran=False,
                    tests_passed=None,
                    test_output=None,
                    inference_called=False,
                    job_id=job_id,
                ),
            )
        manifest, mock_patch, repo = load_sample()
        allowed = manifest["allowed_paths"][0]
        inference_called = False
        try:
            if job["mode"] == "mock":
                diff_text = mock_patch
            elif job["mode"] == "inference":
                if not (
                    self.settings.inference_base_url
                    and self.settings.inference_api_key
                    and self.settings.inference_model
                ):
                    raise InferenceError("inference is not configured")
                inference_called = True
                diff_text = self._infer(manifest, repo, lease_lost.is_set)
            else:
                raise DiffError("unknown mode")
            parsed = parse_unified(diff_text, max_bytes=self.settings.max_diff_bytes)
            if parsed.path not in manifest["allowed_paths"]:
                raise DiffError("path is not allowed for this task")
            with tempfile.TemporaryDirectory(prefix="wrkhorse-ws-") as raw:
                workspace = Path(raw) / "repo"
                copy_tree(repo, workspace)
                original = (workspace / allowed).read_text(encoding="utf-8")
                apply_diff(workspace, allowed, parsed, original)
                tests = None
                if job["mode"] == "mock":
                    tests = run_unittest(
                        workspace, timeout=self.settings.test_timeout_seconds
                    )
        except (DiffError, InferenceError, SandboxError, OSError, UnicodeError) as exc:
            if isinstance(exc, InferenceError) and str(exc) == "inference is not configured":
                code = "inference_not_configured"
                inference_called = False
            elif isinstance(exc, InferenceError):
                code = "inference_failed"
            else:
                code = "diff_rejected"
            return (
                "failed",
                code,
                _result(
                    code=code,
                    diff=None,
                    tests_ran=False,
                    tests_passed=None,
                    test_output=None,
                    inference_called=inference_called,
                    job_id=job_id,
                ),
            )

        if job["mode"] == "inference":
            self._store_diff(job_id, diff_text, None)
            return (
                "diff_ready",
                None,
                _result(
                    code="diff_ready",
                    diff=diff_text,
                    tests_ran=False,
                    tests_passed=None,
                    test_output=None,
                    inference_called=True,
                    job_id=job_id,
                ),
            )

        if tests is None:
            return (
                "failed",
                "diff_rejected",
                _result(
                    code="diff_rejected",
                    diff=diff_text,
                    tests_ran=False,
                    tests_passed=None,
                    test_output=None,
                    inference_called=False,
                    job_id=job_id,
                ),
            )
        self._store_diff(job_id, diff_text, tests.output)
        if not tests.passed:
            code = "test_timeout" if tests.timed_out else "tests_failed"
            return (
                "failed",
                code,
                _result(
                    code=code,
                    diff=diff_text,
                    tests_ran=True,
                    tests_passed=False,
                    test_output=tests.output,
                    inference_called=False,
                    job_id=job_id,
                ),
            )
        return (
            "succeeded",
            None,
            _result(
                code="tests_passed",
                diff=diff_text,
                tests_ran=True,
                tests_passed=True,
                test_output=tests.output,
                inference_called=False,
                job_id=job_id,
            ),
        )

    def _infer(self, manifest: dict, repo: Path, should_stop) -> str:
        if not (
            self.settings.inference_base_url
            and self.settings.inference_api_key
            and self.settings.inference_model
        ):
            raise InferenceError("inference is not configured")
        return request_diff(
            base_url=self.settings.inference_base_url,
            api_key=self.settings.inference_api_key,
            model=self.settings.inference_model,
            prompt=prompt_for(manifest, repo),
            timeout=self.settings.inference_timeout_seconds,
            should_stop=should_stop,
        )

    def _store_diff(self, job_id: str, diff_text: str, test_output: str | None) -> None:
        root = self.settings.artifact_root
        _write_artifact(root, job_id, "diff.patch", diff_text)
        if test_output is not None:
            _write_artifact(root, job_id, "test.txt", test_output)


def worker_id() -> str:
    return f"worker-{os.getpid()}"
