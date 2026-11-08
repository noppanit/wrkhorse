"""Offline checks for the opt-in text-only Ollama MCP tool."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx2
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from wrkhorse.config import ConfigError, load_gateway_settings
from wrkhorse.inference import InferenceError, request_local_chat
from wrkhorse.server import http_app

TOKEN = "r" * 48


class TextOnlyModelTests(unittest.TestCase):
    def test_gateway_tool_is_explicit_opt_in(self):
        with tempfile.TemporaryDirectory() as raw:
            env = {
                "WRKHORSE_AUTH_TOKEN": TOKEN,
                "WRKHORSE_DB": str(Path(raw) / "q.sqlite"),
                "WRKHORSE_MODEL_CHAT_ENABLED": "1",
                "WRKHORSE_INFERENCE_MODEL": "qwen2.5-coder:1.5b",
            }
            with patch.dict(os.environ, env, clear=True):
                settings = load_gateway_settings()
            self.assertTrue(settings.model_chat_enabled)
            self.assertEqual(settings.model_chat_model, "qwen2.5-coder:1.5b")
            with patch.dict(os.environ, {**env, "WRKHORSE_INFERENCE_MODEL": "qwen:cloud"}, clear=True):
                with self.assertRaises(ConfigError):
                    load_gateway_settings()
            with patch.dict(os.environ, {**env, "WRKHORSE_INFERENCE_MODEL": ""}, clear=True):
                with self.assertRaises(ConfigError):
                    load_gateway_settings()

    def test_request_is_fixed_local_and_never_executes(self):
        with patch("wrkhorse.inference._bounded", new_callable=AsyncMock) as mocked:
            mocked.return_value = "A small locally generated answer"
            answer = request_local_chat(
                model="qwen2.5-coder:1.5b", prompt="What is two plus two?", timeout=30
            )
            self.assertEqual(answer, "A small locally generated answer")
            args = mocked.call_args.args
            self.assertEqual(args[0], "http://127.0.0.1:11434/v1/chat/completions")
            self.assertEqual(args[1], "ollama-local")
            self.assertIn("two plus two", args[2]["messages"][1]["content"])
            self.assertFalse(args[2]["stream"])
            self.assertNotIn("tools", args[2])
        with self.assertRaises(InferenceError):
            request_local_chat(model="qwen:cloud", prompt="hello")
        with self.assertRaises(InferenceError):
            request_local_chat(model="qwen2.5-coder:1.5b", prompt="x" * 4097)

    def test_oversized_reply_is_rejected(self):
        with patch("wrkhorse.inference._bounded", new_callable=AsyncMock) as mocked:
            mocked.return_value = "y" * 16_385
            with self.assertRaises(InferenceError):
                request_local_chat(model="qwen2.5-coder:1.5b", prompt="hello")

    def test_tool_is_callable_over_authenticated_local_mcp(self):
        with tempfile.TemporaryDirectory() as raw:
            env = {
                "WRKHORSE_AUTH_TOKEN": TOKEN,
                "WRKHORSE_DB": str(Path(raw) / "queue.sqlite"),
                "WRKHORSE_MODEL_CHAT_ENABLED": "1",
                "WRKHORSE_INFERENCE_MODEL": "qwen2.5-coder:1.5b",
            }
            with patch.dict(os.environ, env, clear=True):
                settings = load_gateway_settings(host="127.0.0.1", port=0)
            app = http_app(settings)
            config = uvicorn.Config(
                app, host="127.0.0.1", port=0, log_level="warning", access_log=False
            )
            server = uvicorn.Server(config)
            worker = threading.Thread(target=server.run, daemon=True)
            worker.start()
            deadline = time.monotonic() + 10
            while not server.started and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(server.started)
            sock = [sock for item in server.servers for sock in item.sockets][0]
            port = sock.getsockname()[1]

            async def call():
                async with httpx2.AsyncClient(
                    headers={"Authorization": f"Bearer {TOKEN}"},
                    timeout=10, trust_env=False,
                ) as client:
                    async with streamable_http_client(
                        f"http://127.0.0.1:{port}/mcp", http_client=client,
                    ) as (read, write):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            listed = await session.list_tools()
                            names = {tool.name for tool in listed.tools}
                            result = await session.call_tool(
                                "ask_local_model", {"prompt": "hello from a client"}
                            )
                            return names, result

            try:
                with patch("wrkhorse.server.request_local_chat", return_value="hello locally") as mocked:
                    names, result = asyncio.run(call())
                self.assertIn("ask_local_model", names)
                self.assertFalse(result.is_error)
                output = json.loads(result.content[0].text)
                self.assertEqual(output["answer"], "hello locally")
                self.assertEqual(output["inference_location"], "localhost")
                self.assertFalse(output["executes_code"])
                mocked.assert_called_once()
            finally:
                server.should_exit = True
                worker.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
