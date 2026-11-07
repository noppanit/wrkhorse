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


class Store:
    def __init__(self, path: Path, *, stale_seconds: float, max_attempts: int) -> None:
        self.path = Path(path)
        self.stale_seconds = stale_seconds
        self.max_attempts = max_attempts
        self.path.parent.mkdir(parents=True, exist_ok=True)
        last_error: sqlite3.OperationalError | None = None
        for _ in range(20):
            try:
                with self.connect() as con:
                    con.executescript(SCHEMA)
                return
            except sqlite3.OperationalError as exc:
                last_error = exc
                if "locked" not in str(exc).lower():
                    raise
                time.sleep(0.05)
        if last_error is not None:
            raise last_error

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA busy_timeout=5000")
            con.execute("PRAGMA foreign_keys=ON")
            yield con
        finally:
            con.close()

    def enqueue(self, task_name: str, mode: str) -> dict:
        if task_name != "sample-add" or mode not in {"mock", "inference"}:
            raise ValueError("unknown task or mode")
        job_id = uuid.uuid4().hex
        now = stamp()
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
        job = self.get(job_id)
        if job is None:
            raise RuntimeError("queued job was not stored")
        return job

    def get(self, job_id: str) -> dict | None:
        with self.connect() as con:
            row = con.execute("SELECT * FROM tasks WHERE job_id = ?", (job_id,)).fetchone()
        return dict(row) if row is not None else None

    def claim(self, worker_id: str) -> dict | None:
        """Claim one queued job, or a stale running job under the attempt cap.

        A running job whose claim is still fresh is invisible to this call.
        """
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

    def touch(self, job_id: str, worker_id: str) -> None:
        now = stamp()
        with self.connect() as con:
            con.execute(
                """
                UPDATE tasks
                SET claimed_at = ?, updated_at = ?
                WHERE job_id = ? AND claimed_by = ? AND status = 'running'
                """,
                (now, now, job_id, worker_id),
            )

    def finish(
        self,
        job_id: str,
        worker_id: str,
        *,
        status: str,
        error: str | None,
        result: dict,
    ) -> bool:
        if status not in TERMINAL:
            raise ValueError("not a terminal status")
        now = stamp()
        payload = json.dumps(result, sort_keys=True)
        with self.connect() as con:
            updated = con.execute(
                """
                UPDATE tasks
                SET status = ?, error = ?, result_json = ?, updated_at = ?
                WHERE job_id = ? AND claimed_by = ? AND status = 'running'
                """,
                (status, error, payload, now, job_id, worker_id),
            )
        return updated.rowcount == 1

    def heartbeat(self, execution_mode: str) -> None:
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

    def project_status(self) -> dict:
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
