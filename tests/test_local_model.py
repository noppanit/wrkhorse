"""Offline checks for the local-model endpoint policy, reasoning output and doctor."""
from __future__ import annotations

import io
import json
import os
import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock, patch

from wrkhorse.config import ConfigError, load_gateway_settings, load_worker_settings
from wrkhorse.diffutil import DiffError, parse_unified
from wrkhorse.doctor import model_listed, run_doctor
from wrkhorse.fixtures import load_sample
from wrkhorse.inference import InferenceError, _parse_content, request_local_chat
from wrkhorse.local_model import LocalModelError, validate_base_url, validate_model_tag

TOKEN = "z" * 48


def _fake_dns(*addresses):
    def resolver(host, *_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, 0)) for a in addresses]
    return patch("wrkhorse.local_model.socket.getaddrinfo", side_effect=resolver)


class EndpointPolicyTests(unittest.TestCase):
    def test_loopback_any_port_is_local(self):
        for url in (
            "http://127.0.0.1:11434/v1",        # Ollama
            "http://127.0.0.1:1234/v1/",        # LM Studio
            "http://127.0.0.1:8080/v1",         # llama.cpp / mlx_lm.server
            "http://[::1]:8000/v1",             # vLLM on IPv6 loopback
        ):
            with self.subTest(url=url):
                _base, scope = validate_base_url(url, allow_lan=False)
                self.assertEqual(scope, "loopback")

    def test_lan_needs_explicit_opt_in(self):
        for url in (
            "http://192.168.1.20:11434/v1",
            "http://10.0.0.5:11434/v1",
            "http://172.20.1.1:11434/v1",
            "http://100.101.102.103:11434/v1",  # Tailscale
            "http://[fd7a:115c:a1e0::1]:11434/v1",  # Tailscale IPv6 ULA
        ):
            with self.subTest(url=url):
                with self.assertRaises(LocalModelError):
                    validate_base_url(url, allow_lan=False)
                self.assertEqual(validate_base_url(url, allow_lan=True)[1], "lan")

    def test_public_metadata_and_wildcard_addresses_always_refused(self):
        for url in (
            "http://8.8.8.8:11434/v1",
            "http://169.254.169.254/v1",
            "http://0.0.0.0:11434/v1",
            "http://224.0.0.1/v1",
            "https://api.openai.com/v1",
            "http://localhost:11434/v1",
            "http://user:pw@192.168.1.2:11434/v1",
            "http://192.168.1.2:11434/v1?x=1",
            "http://192.168.1.2:11434/api",
            "ftp://192.168.1.2/v1",
        ):
            with self.subTest(url=url), self.assertRaises(LocalModelError):
                validate_base_url(url, allow_lan=True)

    def test_hostnames_need_private_suffix_and_private_resolution(self):
        with _fake_dns("192.168.1.30"):
            self.assertEqual(
                validate_base_url("http://studio.local:11434/v1", allow_lan=True)[1], "lan"
            )
        with _fake_dns("100.90.1.2"):
            validate_base_url("http://gpu-box.tail1234.ts.net:11434/v1", allow_lan=True)
        with _fake_dns("93.184.216.34"), self.assertRaises(LocalModelError):
            validate_base_url("http://evil.local:11434/v1", allow_lan=True)
        with _fake_dns("192.168.1.30", "93.184.216.34"), self.assertRaises(LocalModelError):
            validate_base_url("http://mixed.lan:11434/v1", allow_lan=True)
        with _fake_dns("192.168.1.30"), self.assertRaises(LocalModelError):
            validate_base_url("http://gpu.example.com:11434/v1", allow_lan=True)
        with _fake_dns("192.168.1.30"), self.assertRaises(LocalModelError):
            validate_base_url("http://studio.local:11434/v1", allow_lan=False)
        with patch("wrkhorse.local_model.socket.getaddrinfo", side_effect=OSError("nx")):
            with self.assertRaises(LocalModelError):
                validate_base_url("http://asleep.local:11434/v1", allow_lan=True)

    def test_ollama_cloud_tags_are_refused(self):
        for tag in ("qwen3-coder:cloud", "gpt-oss:120b-cloud", "deepseek-v3.1:671b-cloud", "", "bad tag"):
            with self.subTest(tag=tag), self.assertRaises(LocalModelError):
                validate_model_tag(tag)
        for tag in ("qwen2.5-coder:1.5b", "llama3.2", "hf.co/org/model:Q4_K_M", "qwen3:8b"):
            with self.subTest(tag=tag):
                self.assertEqual(validate_model_tag(tag), tag)


class SettingsTests(unittest.TestCase):
    def _env(self, raw, **extra):
        return patch.dict(os.environ, {
            "WRKHORSE_DB": str(Path(raw) / "q.sqlite"),
            "WRKHORSE_AUTH_TOKEN": TOKEN,
            "WRKHORSE_EXECUTION_MODE": "inference-only",
            "WRKHORSE_MODEL_CHAT_ENABLED": "1",
            "WRKHORSE_INFERENCE_MODEL": "qwen2.5-coder:7b",
            **extra,
        }, clear=True)

    def test_worker_and_gateway_share_a_lan_endpoint(self):
        url = "http://192.168.1.40:11434/v1"
        with tempfile.TemporaryDirectory() as raw:
            with self._env(raw, WRKHORSE_INFERENCE_BASE_URL=url):
                with self.assertRaises(ConfigError):
                    load_worker_settings()
                with self.assertRaises(ConfigError):
                    load_gateway_settings()
            with self._env(raw, WRKHORSE_INFERENCE_BASE_URL=url, WRKHORSE_ALLOW_LAN_MODEL="1"):
                worker = load_worker_settings()
                gateway = load_gateway_settings()
        self.assertEqual(worker.inference_base_url, url)
        self.assertEqual(worker.inference_scope, "lan")
        self.assertEqual(worker.inference_api_key, "ollama-local")
        self.assertEqual(gateway.model_chat_base_url, url)
        self.assertEqual(gateway.model_chat_scope, "lan")

    def test_gateway_chat_defaults_to_local_ollama_and_max_tokens(self):
        with tempfile.TemporaryDirectory() as raw:
            with self._env(raw, WRKHORSE_INFERENCE_MAX_TOKENS="4096"):
                gateway = load_gateway_settings()
                with self.assertRaises(ConfigError):
                    load_worker_settings()  # the worker needs the URL spelled out
            with self._env(raw, WRKHORSE_INFERENCE_MAX_TOKENS="4096",
                           WRKHORSE_INFERENCE_BASE_URL="http://127.0.0.1:1234/v1"):
                worker = load_worker_settings()
            with self._env(raw, WRKHORSE_INFERENCE_MODEL="gpt-oss:120b-cloud"):
                with self.assertRaises(ConfigError):
                    load_gateway_settings()
            with self._env(raw, WRKHORSE_INFERENCE_MAX_TOKENS="99999"):
                with self.assertRaises(ConfigError):
                    load_gateway_settings()
        self.assertEqual(worker.inference_max_tokens, 4096)
        self.assertEqual(worker.inference_scope, "loopback")
        self.assertEqual(gateway.model_chat_base_url, "http://127.0.0.1:11434/v1")
        self.assertEqual(gateway.model_chat_scope, "loopback")
        self.assertEqual(gateway.model_max_tokens, 4096)


class ChatEndpointTests(unittest.TestCase):
    def test_chat_uses_the_configured_local_endpoint_only(self):
        with patch("wrkhorse.inference._bounded", new_callable=AsyncMock) as mocked:
            mocked.return_value = "hi"
            request_local_chat(
                model="qwen3:8b", prompt="hello",
                base_url="http://192.168.1.40:11434/v1", allow_lan=True, max_tokens=2048,
            )
            self.assertEqual(mocked.call_args.args[0], "http://192.168.1.40:11434/v1/chat/completions")
            self.assertEqual(mocked.call_args.args[2]["max_tokens"], 2048)
        for kwargs in (
            {"base_url": "https://api.openai.com/v1", "allow_lan": True},
            {"base_url": "http://192.168.1.40:11434/v1", "allow_lan": False},
        ):
            with self.subTest(**kwargs), self.assertRaises(InferenceError):
                request_local_chat(model="qwen3:8b", prompt="hello", **kwargs)


class ReasoningOutputTests(unittest.TestCase):
    @staticmethod
    def _completion(content, finish="stop"):
        return json.dumps({"choices": [{"message": {"content": content}, "finish_reason": finish}]}).encode()

    def test_think_block_is_removed_before_the_diff(self):
        diff = load_sample()[1]
        content = _parse_content(self._completion("<think>\nadd, not subtract\n</think>\n" + diff))
        self.assertEqual(content, diff)
        parse_unified(content, max_bytes=32768)

    def test_unfinished_thinking_reports_truncation(self):
        with self.assertRaisesRegex(InferenceError, "truncated"):
            _parse_content(self._completion("<think>still going", finish="length"))
        with self.assertRaisesRegex(InferenceError, "empty"):
            _parse_content(self._completion(None))


class FenceTests(unittest.TestCase):
    def test_prose_around_one_fenced_diff_is_tolerated(self):
        diff = load_sample()[1]
        text = "Here is the fix:\n\n```diff\n" + diff + "```\nThis changes - to +."
        parsed = parse_unified(text, max_bytes=32768)
        self.assertEqual(parsed.path, "adder/mathy.py")

    def test_two_fenced_blocks_are_ambiguous_and_rejected(self):
        diff = load_sample()[1]
        text = "A:\n```diff\n" + diff + "```\nB:\n```diff\n" + diff + "```\n"
        with self.assertRaises(DiffError):
            parse_unified(text, max_bytes=32768)


class _Models(BaseHTTPRequestHandler):
    ids: list[str] = []

    def do_GET(self):
        body = json.dumps({"object": "list", "data": [{"id": i} for i in self.ids]}).encode()
        self.send_response(200 if self.path == "/v1/models" else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class DoctorTests(unittest.TestCase):
    def test_model_listed_handles_latest(self):
        self.assertTrue(model_listed("llama3.2", ["llama3.2:latest"]))
        self.assertTrue(model_listed("llama3.2:latest", ["llama3.2"]))
        self.assertFalse(model_listed("qwen3:8b", ["qwen3:4b"]))

    def test_doctor_against_a_stub_server(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Models)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        port = server.server_address[1]
        try:
            for ids, model, expected in (
                (["qwen2.5-coder:1.5b"], "qwen2.5-coder:1.5b", 0),
                (["llama3.2:latest"], "qwen2.5-coder:1.5b", 2),
            ):
                _Models.ids = ids
                out = io.StringIO()
                env = {
                    "WRKHORSE_INFERENCE_BASE_URL": f"http://127.0.0.1:{port}/v1",
                    "WRKHORSE_INFERENCE_MODEL": model,
                }
                with self.subTest(ids=ids), patch.dict(os.environ, env, clear=True):
                    code = run_doctor(warm=False, out=lambda line: print(line, file=out))
                    self.assertEqual(code, expected, out.getvalue())
        finally:
            server.shutdown()
            server.server_close()

    def test_doctor_reports_unreachable_and_refused_urls(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            free = probe.getsockname()[1]
        for url in (f"http://127.0.0.1:{free}/v1", "https://api.openai.com/v1"):
            lines = []
            with self.subTest(url=url), patch.dict(os.environ, {
                "WRKHORSE_INFERENCE_BASE_URL": url,
                "WRKHORSE_INFERENCE_MODEL": "qwen2.5-coder:1.5b",
            }, clear=True):
                self.assertEqual(run_doctor(warm=False, out=lines.append), 2)
                self.assertTrue(any(line.startswith("FAIL") for line in lines))


if __name__ == "__main__":
    unittest.main()
