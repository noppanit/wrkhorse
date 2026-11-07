"""SQLite WAL queue shared by the gateway and the worker."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

HEARTBEAT_FRESH_SECONDS = 15
# One store call waits at most four one-second lock attempts, plus this backoff.
# That stays under the old five-second failure and does not grow with the task.
_LOCK_BACKOFF_SECONDS = (0.0, 0.05, 0.1, 0.2)

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    job_id TEXT PRIMARY KEY,
    task_name TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL,
    claimed_by TEXT,
    claimed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    error TEXT,
    result_json TEXT
);
CREATE TABLE IF NOT EXISTS heartbeats (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    execution_mode TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
"""

TERMINAL = {"succeeded", "diff_ready", "failed", "refused"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def stamp(moment: datetime | None = None) -> str:
    return (moment or utcnow()).isoformat()


def is_busy(exc: BaseException) -> bool:
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    text = str(exc).lower()
    return "locked" in text or "busy" in text


class Store:
    def __init__(self, path: Path, *, stale_seconds: float, max_attempts: int) -> None:
        self.path = Path(path)
        self.stale_seconds = stale_seconds
        self.max_attempts = max_attempts
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._retry(self._migrate)

    def _migrate(self) -> None:
        with self.connect() as con:
            con.executescript(SCHEMA)

    def _retry(self, fn):
        last: sqlite3.OperationalError | None = None
        for delay in _LOCK_BACKOFF_SECONDS:
            if delay:
                time.sleep(delay)
            try:
                return fn()
            except sqlite3.OperationalError as exc:
                if not is_busy(exc):
                    raise
                last = exc
        if last is not None:
            raise last
        raise sqlite3.OperationalError("database is locked")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.path, timeout=1, isolation_level=None)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA busy_timeout=1000")
            con.execute("PRAGMA foreign_keys=ON")
            yield con
        finally:
            con.close()

    def enqueue(self, task_name: str, mode: str) -> dict:
        if task_name != "sample-add" or mode not in {"mock", "inference"}:
            raise ValueError("unknown task or mode")
        job_id = uuid.uuid4().hex
        now = stamp()

        def op() -> dict:
            with self.connect() as con:
                con.execute(
                    """
                    INSERT INTO tasks (
                        job_id, task_name, mode, status, attempts, max_attempts,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, 'queued', 0, ?, ?, ?)
                    """,
                    (job_id, task_name, mode, self.max_attempts, now, now),
                )
                row = con.execute("SELECT * FROM tasks WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                raise RuntimeError("queued job was not stored")
            return dict(row)

        return self._retry(op)

    def get(self, job_id: str) -> dict | None:
        def op() -> dict | None:
            with self.connect() as con:
                row = con.execute("SELECT * FROM tasks WHERE job_id = ?", (job_id,)).fetchone()
            return dict(row) if row is not None else None

        return self._retry(op)

    def claim(self, worker_id: str) -> dict | None:
        """Claim one queued job, or a stale running job under the attempt cap.

        A running job whose claim is still fresh is invisible to this call.
        """
        def op() -> dict | None:
            return self._claim_once(worker_id)

        return self._retry(op)

    def _claim_once(self, worker_id: str) -> dict | None:
        now_dt = utcnow()
        now = stamp(now_dt)
        stale_before = stamp(now_dt - timedelta(seconds=self.stale_seconds))
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                claimed: dict | None = None
                for _ in range(10):
                    row = con.execute(
                        """
                        SELECT job_id, attempts, max_attempts, status
                        FROM tasks
                        WHERE status = 'queued'
                           OR (status = 'running' AND claimed_at < ?)
                        ORDER BY created_at
                        LIMIT 1
                        """,
                        (stale_before,),
                    ).fetchone()
                    if row is None:
                        break
                    if row["attempts"] >= row["max_attempts"]:
                        con.execute(
                            """
                            UPDATE tasks
                            SET status = 'failed', error = 'attempts_exhausted', updated_at = ?
                            WHERE job_id = ? AND status = 'running'
                            """,
                            (now, row["job_id"]),
                        )
                        continue
                    updated = con.execute(
                        """
                        UPDATE tasks
                        SET status = 'running',
                            claimed_by = ?,
                            claimed_at = ?,
                            attempts = attempts + 1,
                            updated_at = ?,
                            error = NULL
                        WHERE job_id = ?
                          AND attempts < max_attempts
                          AND (
                              status = 'queued'
                              OR (status = 'running' AND claimed_at < ?)
                          )
                        """,
                        (worker_id, now, now, row["job_id"], stale_before),
                    )
                    if updated.rowcount == 1:
                        stored = con.execute(
                            "SELECT * FROM tasks WHERE job_id = ?", (row["job_id"],)
                        ).fetchone()
                        claimed = dict(stored)
                        break
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise
        return claimed

    def touch(self, job_id: str, worker_id: str, attempts: int) -> bool:
        """Renew one claim. False means this attempt no longer owns the job."""

        def op() -> bool:
            now = stamp()
            with self.connect() as con:
                updated = con.execute(
                    """
                    UPDATE tasks
                    SET claimed_at = ?, updated_at = ?
                    WHERE job_id = ? AND claimed_by = ? AND attempts = ? AND status = 'running'
                    """,
                    (now, now, job_id, worker_id, attempts),
                )
            return updated.rowcount == 1

        return self._retry(op)

    def finish(
        self,
        job_id: str,
        worker_id: str,
        attempts: int,
        *,
        status: str,
        error: str | None,
        result: dict,
    ) -> bool:
        """Finish only the attempt that still owns the job."""
        if status not in TERMINAL:
            raise ValueError("not a terminal status")
        payload = json.dumps(result, sort_keys=True)

        def op() -> bool:
            now = stamp()
            with self.connect() as con:
                updated = con.execute(
                    """
                    UPDATE tasks
                    SET status = ?, error = ?, result_json = ?, updated_at = ?
                    WHERE job_id = ? AND claimed_by = ? AND attempts = ? AND status = 'running'
                    """,
                    (status, error, payload, now, job_id, worker_id, attempts),
                )
            return updated.rowcount == 1

        return self._retry(op)

    def heartbeat(self, execution_mode: str) -> None:
        def op() -> None:
            now = stamp()
            with self.connect() as con:
                con.execute(
                    """
                    INSERT INTO heartbeats (id, execution_mode, last_seen)
                    VALUES (1, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        execution_mode = excluded.execution_mode,
                        last_seen = excluded.last_seen
                    """,
                    (execution_mode, now),
                )

        self._retry(op)

    def project_status(self) -> dict:
        return self._retry(self._project_status_once)

    def _project_status_once(self) -> dict:
        with self.connect() as con:
            row = con.execute("SELECT * FROM heartbeats WHERE id = 1").fetchone()
        connected = False
        mode = None
        if row is not None:
            seen = datetime.fromisoformat(row["last_seen"])
            connected = utcnow() - seen < timedelta(seconds=HEARTBEAT_FRESH_SECONDS)
            if connected:
                mode = row["execution_mode"]
        return {
            "service": "wrkhorse",
            "slice": "mcp-queue-and-coding-worker",
            "tools": [
                "submit_task",
                "get_task_status",
                "get_task_result",
                "get_project_status",
            ],
            "registered_tasks": ["sample-add"],
            "research_worker": "not_implemented",
            "code_execution_default": "disabled",
            "subprocess_is_sandbox": False,
            "executes_model_generated_code": False,
            "remote_connector": "unverified",
            "gpu": "unverified",
            "worker_connected": connected,
            "worker_execution_mode": mode,
            "merges_deploys_purchases": "not_performed",
        }


def public_status(job: dict) -> dict:
    return {
        "job_id": job["job_id"],
        "task_name": job["task_name"],
        "mode": job["mode"],
        "status": job["status"],
        "attempts": job["attempts"],
        "max_attempts": job["max_attempts"],
        "error": job["error"],
        "ready": job["status"] in TERMINAL,
        "updated_at": job["updated_at"],
    }


def public_result(job: dict) -> dict:
    status = public_status(job)
    if job["status"] not in TERMINAL:
        return status
    result = json.loads(job["result_json"] or "{}")
    status["result"] = result
    return status
