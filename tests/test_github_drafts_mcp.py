"""Check Pi-style draft tools over authenticated Streamable HTTP MCP (no GitHub network)."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import httpx2
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from wrkhorse.config import load_gateway_settings
from wrkhorse.server import http_app

MCP_TOKEN = "t" * 48
GITHUB_TOKEN = "github_pat_" + "g" * 35


class FakeDraftService:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.hits = []

    def read_file(self, path, draft_id):
        self.hits.append(("read", path, draft_id))
        return {"content": "# Safe draft", "path": path, "branch": "main", "sha": "a" * 40}

    def write_file(self, path, content, title):
        self.hits.append(("write", path, content, title))
        return {"draft_id": "a" * 32, "status": "draft_pr",
                "draft_pr_url": "https://github.com/noppanit/wrkhorse/pull/123",
                "requires_human_review": True, "merged": False}

    def edit_file(self, draft_id, old_text, new_text):
        self.hits.append(("edit", draft_id, old_text, new_text))
        return {"status": "draft_updated", "requires_human_review": True, "merged": False}


class GithubDraftsMCPTests(unittest.TestCase):
    def test_new_tools_visible_only_with_explicit_optin(self):
        with tempfile.TemporaryDirectory() as raw:
            env = {
                "WRKHORSE_AUTH_TOKEN": MCP_TOKEN,
                "WRKHORSE_DB": str(Path(raw) / "q.sqlite"),
                "WRKHORSE_GITHUB_DRAFTS_ENABLED": "1",
                "WRKHORSE_GITHUB_DRAFT_REPO": "noppanit/wrkhorse",
                "WRKHORSE_GITHUB_TOKEN": GITHUB_TOKEN,
            }
            with patch.dict(os.environ, env, clear=True):
                settings = load_gateway_settings(host="127.0.0.1", port=0)
            fake = FakeDraftService()
            with patch("wrkhorse.server.GitHubDrafts", return_value=fake):
                app = http_app(settings)
            config = uvicorn.Config(app, host="127.0.0.1", port=0,
                                    access_log=False, log_level="warning")
            server = uvicorn.Server(config)
            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()
            deadline = time.monotonic() + 10
            while not server.started and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(server.started)
            sockets = [sock for item in server.servers for sock in item.sockets]
            port = sockets[0].getsockname()[1]

            async def call():
                async with httpx2.AsyncClient(
                    headers={"Authorization": f"Bearer {MCP_TOKEN}"},
                    timeout=15, trust_env=False,
                ) as http:
                    async with streamable_http_client(
                        f"http://127.0.0.1:{port}/mcp", http_client=http,
                    ) as (read, write):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            names = {t.name for t in (await session.list_tools()).tools}
                            result = await session.call_tool(
                                "write_file",
                                {"path": "posts/drafts/hello.md", "content": "# Hello",
                                 "title": "Hello"},
                            )
                            read_result = await session.call_tool(
                                "read_file", {"path": "posts/drafts/hello.md"},
                            )
                            edited = await session.call_tool(
                                "edit_file",
                                {"draft_id": "a" * 32, "old_text": "old", "new_text": "new"},
                            )
                            return names, result, read_result, edited

            try:
                with self.assertRaises(HTTPError) as denied:
                    urlopen(Request(f"http://127.0.0.1:{port}/mcp", data=b"{}",
                                    method="POST"), timeout=5)
                self.assertEqual(denied.exception.code, 401)
                names, created, read, edited = asyncio.run(call())
            finally:
                server.should_exit = True
                thread.join(timeout=5)
            self.assertTrue({"read_file", "write_file", "edit_file"}.issubset(names))
            self.assertFalse(created.is_error)
            self.assertTrue(json.loads(created.content[0].text)["requires_human_review"])
            self.assertFalse(read.is_error)
            self.assertFalse(edited.is_error)
            self.assertEqual([hit[0] for hit in fake.hits], ["write", "read", "edit"])
            self.assertNotIn(GITHUB_TOKEN, str(fake.hits))


if __name__ == "__main__":
    unittest.main()
