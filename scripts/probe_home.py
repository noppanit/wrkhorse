"""Local MCP smoke test for the MacBook deployment.

Run in a *second* Terminal while scripts/start-home.sh is running.
No remote URL accepted: don't accidentally send your local token to a host.
Use --submit to explicitly enqueue a synthetic local inference job.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def _payload(result):
    if result.is_error:
        raise RuntimeError("MCP tool returned an error")
    if not result.content or not hasattr(result.content[0], "text"):
        raise RuntimeError("MCP returned no text")
    data = json.loads(result.content[0].text)
    if not isinstance(data, dict):
        raise RuntimeError("MCP returned unexpected JSON")
    return data


def _local_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "http" or parts.hostname not in ("127.0.0.1", "localhost"):
        raise ValueError("Probe only permits loopback MCP URLs; never send your token to a cloud URL")
    if parts.username or parts.password or parts.query or parts.fragment or parts.path != "/mcp":
        raise ValueError("MCP URL must be a plain http://127.0.0.1:PORT/mcp")
    return url


async def check(url: str, token: str, submit: bool) -> None:
    async with httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=20, follow_redirects=False, trust_env=False,
    ) as http:
        async with streamable_http_client(url, http_client=http) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                names = {t.name for t in listed.tools}
                needed = {"get_project_status", "submit_task", "get_task_status", "get_task_result"}
                if not needed.issubset(names):
                    raise RuntimeError("MCP server did not expose the required wrkhorse tools")
                project = _payload(await session.call_tool("get_project_status", {}))
                print("Authenticated local MCP call succeeded.")
                print("Tools:", ", ".join(sorted(names)))
                print("Project status keys:", ", ".join(sorted(project)))
                if not submit:
                    print("Read-only probe complete. Re-run with --submit to call local Ollama.")
                    return
                task = _payload(await session.call_tool(
                    "submit_task", {"task_name": "sample-add", "mode": "inference"}
                ))
                job_id = task["job_id"]
                print("Submitted sample-add inference job:", job_id)
                for _ in range(180):
                    status = _payload(await session.call_tool("get_task_status", {"job_id": job_id}))
                    current = status.get("status")
                    if current in ("diff_ready", "failed", "refused", "succeeded"):
                        break
                    await asyncio.sleep(1)
                else:
                    raise RuntimeError("Task still pending after 180 seconds")
                print("Final task status:", current)
                output = _payload(await session.call_tool("get_task_result", {"job_id": job_id}))
                result = output.get("result") or {}
                if current == "diff_ready":
                    print("Proposed diff from your LOCAL Ollama model:")
                    print(result.get("diff", ""))
                    print("No model-generated code was executed or merged.")
                else:
                    print("Result code:", result.get("code") or output.get("error"))
                    raise RuntimeError("Local model task did not return a valid diff")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--submit", action="store_true", help="Explicitly call the local model")
    p.add_argument("--url", default="http://127.0.0.1:8765/mcp")
    args = p.parse_args()
    url = _local_url(args.url)
    path = Path(__file__).resolve().parents[1] / "var" / "mcp-token"
    token = path.read_text(encoding="utf-8").strip()
    asyncio.run(check(url, token, args.submit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
