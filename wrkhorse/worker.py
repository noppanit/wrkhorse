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
from wrkhorse.diffutil import DiffError, apply_diff, parse_unified, strip_fences
from wrkhorse.executor import run_unittest
from wrkhorse.fixtures import load_sample, prompt_for
from wrkhorse.inference import InferenceError, request_diff
from wrkhorse.model_router import load_router
from wrkhorse.logsetup import get_logger
from wrkhorse.sandbox import SandboxError, copy_tree
from wrkhorse.store import Store, is_busy

log = get_logger(__name__)
_JOB_ID = re.compile(r"^[0-9a-f]{32}$")
_ARTIFACTS = {"diff.patch", "test.txt", "result.json"}


def attempt_artifact_dir(job_id: str, attempt: int) -> str:
    if _JOB_ID.fullmatch(job_id) is None or not isinstance(attempt, int) or attempt < 1:
        raise RuntimeError("refusing artifact path")
    return f"artifacts/{job_id}/attempt-{attempt}"


def _write_artifact(root: Path, job_id: str, attempt: int, name: str, text: str) -> None:
    relative = attempt_artifact_dir(job_id, attempt)
    if name not in _ARTIFACTS:
        raise RuntimeError("refusing artifact path")
    dest_dir = root / job_id / f"attempt-{attempt}"
    if dest_dir != root.joinpath(*relative.split("/")[1:]):
        raise RuntimeError("refusing artifact path")
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
    attempts: int | None = None,
) -> dict:
    recorded = None
    if attempts is not None and (diff is not None or test_output is not None):
        recorded = attempt_artifact_dir(job_id, attempts)
    return {
        "code": code,
        "diff": diff,
        "tests_ran": tests_ran,
        "tests_passed": tests_passed,
        "test_output": test_output,
        "inference_called": inference_called,
        "executes_model_generated_code": False,
        "subprocess_is_sandbox": False,
        "artifact_dir": recorded,
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
        if self.settings.execution_mode not in {"trusted-local-demo", "inference-only"}:
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
        if self.settings.execution_mode == "inference-only" and job["mode"] != "inference":
            return (
                "refused",
                "inference_only",
                _result(
                    code="inference_only",
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
            # Store/return the extracted diff, not the model's prose around it.
            diff_text = strip_fences(diff_text)
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
            # Our own messages only (never model text, prompts or keys). Without
            # this the owner sees "inference_failed" and cannot tell a cold
            # model, a refused connection and a malformed diff apart.
            log.warning("job %s: %s (%s)", job_id, type(exc).__name__, exc)
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

        attempts = int(job["attempts"])
        if job["mode"] == "inference":
            self._store_diff(job_id, attempts, diff_text, None)
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
                    attempts=attempts,
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
        self._store_diff(job_id, int(job["attempts"]), diff_text, tests.output)
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
                    attempts=int(job["attempts"]),
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
                attempts=int(job["attempts"]),
            ),
        )

    def _infer(self, manifest: dict, repo: Path, should_stop) -> str:
        if not (
            self.settings.inference_base_url
            and self.settings.inference_api_key
            and self.settings.inference_model
        ):
            raise InferenceError("inference is not configured")
        router = load_router(
            local_url=self.settings.inference_base_url,
            local_model=self.settings.inference_model,
            allow_lan=self.settings.inference_scope == "lan",
        )
        prompt = (
            prompt_for(manifest, repo)
            + "\nThis original Python file has EXACTLY TWO lines. "
            + "For the one-line replacement, use a counted hunk "
            + "@@ -1,2 +1,2 @@ with one context line, one removed "
            + "line and one added line. Return no markdown fences."
        )
        last_error = None
        for route in router.candidates("coding"):
            if should_stop():
                raise InferenceError("inference lease lost")
            try:
                return request_diff(
                    base_url=route.base_url,
                    api_key=self.settings.inference_api_key,
                    model=route.model,
                    prompt=prompt,
                    timeout=self.settings.inference_timeout_seconds,
                    should_stop=should_stop,
                    max_tokens=self.settings.inference_max_tokens,
                )
            except InferenceError as exc:
                last_error = exc
                if should_stop():
                    raise
                # Automatic cloud -> local fallback, never retry the paid
                # endpoint. Errors are sanitized by the inference client.
                continue
        raise last_error or InferenceError("no inference route available")

    def _store_diff(
        self, job_id: str, attempt: int, diff_text: str, test_output: str | None
    ) -> None:
        root = self.settings.artifact_root
        _write_artifact(root, job_id, attempt, "diff.patch", diff_text)
        if test_output is not None:
            _write_artifact(root, job_id, attempt, "test.txt", test_output)


def worker_id() -> str:
    return f"worker-{os.getpid()}"
