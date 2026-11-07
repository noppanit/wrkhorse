"""Explicit local demo. It is not the default remote execution mode."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import time
from pathlib import Path

import anyio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from wrkhorse.fixtures import REPO_ROOT, load_sample

_TOKEN_ENV = "WRKHORSE_AUTH_TOKEN"


def _payload(result) -> dict:
    if result.is_error:
        text = "".join(getattr(block, "text", "") for block in result.content)
        raise RuntimeError(f"demo tool failed: {text}")
    import json

    return json.loads("".join(getattr(block, "text", "") for block in result.content))


def _child_env(extra: dict[str, str]) -> dict[str, str]:
    env = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "HOME": extra.get("HOME", "/tmp"),
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    env.update(extra)
    return env


async def _roundtrip(db: Path, token: str) -> dict:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "wrkhorse", "mcp-stdio"],
        cwd=str(REPO_ROOT),
        env=_child_env(
            {
                "HOME": str(db.parent),
                "WRKHORSE_DB": str(db),
                _TOKEN_ENV: token,
            }
        ),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            names = sorted(tool.name for tool in listed.tools)
            submitted = _payload(
                await session.call_tool(
                    "submit_task",
                    {"task_name": "sample-add", "mode": "mock"},
                )
            )
            job_id = submitted["job_id"]
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                current = _payload(
                    await session.call_tool("get_task_status", {"job_id": job_id})
                )
                if current["ready"]:
                    result = _payload(
                        await session.call_tool("get_task_result", {"job_id": job_id})
                    )
                    result["tools"] = names
                    return result
                await anyio.sleep(0.1)
    raise RuntimeError("demo timed out waiting for the worker")


def run_demo() -> str:
    import secrets

    _, expected, _ = load_sample()
    demo_dir = REPO_ROOT / "var" / "demo"
    if demo_dir.exists():
        shutil.rmtree(demo_dir)
    demo_dir.mkdir(parents=True)
    db = demo_dir / "wrkhorse.sqlite"
    token = secrets.token_urlsafe(32)
    log_path = demo_dir / "worker.log"
    log_handle = log_path.open("w", encoding="utf-8")
    worker = subprocess.Popen(
        [sys.executable, "-m", "wrkhorse", "worker"],
        cwd=REPO_ROOT,
        env=_child_env(
            {
                "HOME": str(demo_dir),
                "WRKHORSE_DB": str(db),
                "WRKHORSE_EXECUTION_MODE": "trusted-local-demo",
                "WRKHORSE_POLL_SECONDS": "0.05",
            }
        ),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        result = asyncio.run(_roundtrip(db, token))
    finally:
        worker.terminate()
        try:
            worker.wait(timeout=5)
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.wait(timeout=5)
        log_handle.close()
    body = result.get("result") or {}
    diff = body.get("diff") or ""
    if result.get("status") != "succeeded" or body.get("tests_passed") is not True:
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        log_text = log_text.replace(token, "[redacted]")
        raise RuntimeError(
            "demo did not pass the fixture\n"
            + log_text[-2000:]
            + "\n"
            + str({key: result.get(key) for key in ("status", "error")})
        )
    if diff != expected:
        raise RuntimeError("demo diff did not match the reviewed fixture patch")
    output = body.get("test_output") or ""
    lines = [
        "slice: local trusted demo (not a remote connector and not a GPU run)",
        "task_name: sample-add",
        "mode: mock",
        f"status: {result['status']}",
        "tests_ran: true",
        "tests_passed: true",
        "inference_called: false",
        "executes_model_generated_code: false",
        f"tools: {', '.join(result['tools'])}",
        "diff:",
        diff.rstrip("\n"),
        "test_output:",
        output.rstrip("\n"),
        f"artifact: var/demo/artifacts/{result['job_id']}/diff.patch",
        "",
    ]
    return "\n".join(lines)
