"""Offline unit checks for the MacBook-only Ollama deployment path."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wrkhorse.config import ConfigError, load_gateway_settings, load_worker_settings
from wrkhorse.fixtures import load_sample
from wrkhorse.server import _security
from wrkhorse.store import Store, public_result
from wrkhorse.worker import Worker

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "m" * 48
URL = "http://127.0.0.1:11434/v1"


def _env(path, **extra):
    return patch.dict(os.environ, {
        "WRKHORSE_DB": str(path),
        "WRKHORSE_EXECUTION_MODE": "inference-only",
        "WRKHORSE_INFERENCE_BASE_URL": URL,
        "WRKHORSE_INFERENCE_MODEL": "qwen2.5-coder:1.5b",
        "WRKHORSE_AUTH_TOKEN": TOKEN,
        **extra,
    }, clear=True)


class HomeDeploymentTests(unittest.TestCase):
    def test_inference_only_defaults_to_local_dummy_credential(self):
        with tempfile.TemporaryDirectory() as raw:
            with _env(Path(raw) / "tasks.sqlite"):
                settings = load_worker_settings()
        self.assertEqual(settings.execution_mode, "inference-only")
        self.assertEqual(settings.inference_base_url, URL)
        self.assertEqual(settings.inference_api_key, "ollama-local")

    def test_refuses_remote_inference_cloud_tags_and_real_keys(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "tasks.sqlite"
            for overrides in (
                {"WRKHORSE_INFERENCE_BASE_URL": "https://other-host.example/v1"},
                {"WRKHORSE_INFERENCE_BASE_URL": "http://localhost:11434/v1"},
                {"WRKHORSE_INFERENCE_MODEL": "qwen2.5-coder:cloud"},
                {"WRKHORSE_INFERENCE_API_KEY": "sensitive-real-key"},
                {"WRKHORSE_INFERENCE_MODEL": ""},
            ):
                with self.subTest(overrides=overrides):
                    with _env(path, **overrides), self.assertRaises(ConfigError):
                        load_worker_settings()

    def test_no_mock_test_subprocess_or_model_called(self):
        with tempfile.TemporaryDirectory() as raw:
            with _env(Path(raw) / "tasks.sqlite"):
                settings = load_worker_settings()
            store = Store(settings.db_path, stale_seconds=30, max_attempts=2)
            job = store.enqueue("sample-add", "mock")
            with patch("wrkhorse.worker.run_unittest", side_effect=AssertionError("test was run")), patch(
                "wrkhorse.worker.request_diff", side_effect=AssertionError("model was called")
            ):
                self.assertTrue(Worker(settings, store, "mac-worker").once())
            saved = store.get(job["job_id"])
            self.assertEqual(saved["status"], "refused")
            self.assertEqual(saved["error"], "inference_only")

    def test_model_returns_diff_without_running_generated_code(self):
        reviewed_diff = load_sample()[1]
        with tempfile.TemporaryDirectory() as raw:
            with _env(Path(raw) / "tasks.sqlite"):
                settings = load_worker_settings()
            store = Store(settings.db_path, stale_seconds=30, max_attempts=2)
            job = store.enqueue("sample-add", "inference")
            with patch("wrkhorse.worker.run_unittest", side_effect=AssertionError("not allowed")), patch(
                "wrkhorse.worker.request_diff", return_value=reviewed_diff
            ) as infer:
                self.assertTrue(Worker(settings, store, "mac-worker").once())
            saved = store.get(job["job_id"])
            outcome = public_result(saved)["result"]
        self.assertEqual(saved["status"], "diff_ready")
        self.assertTrue(outcome["inference_called"])
        self.assertFalse(outcome["tests_ran"])
        self.assertFalse(outcome["executes_model_generated_code"])
        self.assertEqual(infer.call_args.kwargs["base_url"], URL)
        self.assertIn("EXACTLY TWO lines", infer.call_args.kwargs["prompt"])

    def test_invalid_model_diff_is_rejected_not_executed(self):
        with tempfile.TemporaryDirectory() as raw:
            with _env(Path(raw) / "tasks.sqlite"):
                settings = load_worker_settings()
            store = Store(settings.db_path, stale_seconds=30, max_attempts=2)
            job = store.enqueue("sample-add", "inference")
            with patch("wrkhorse.worker.run_unittest", side_effect=AssertionError("not allowed")), patch(
                "wrkhorse.worker.request_diff", return_value="rm -rf /"
            ):
                Worker(settings, store, "mac-worker").once()
            saved = store.get(job["job_id"])
        self.assertEqual(saved["status"], "failed")
        self.assertEqual(saved["error"], "diff_rejected")

    def test_external_tunnel_hostname_has_strict_host_check_even_on_loopback(self):
        with tempfile.TemporaryDirectory() as raw:
            with _env(Path(raw) / "tasks.sqlite", WRKHORSE_PUBLIC_HOST="agent.example.org"):
                gateway = load_gateway_settings(host="127.0.0.1", port=8765)
            security = _security(gateway)
            self.assertIsNotNone(security)
            self.assertTrue(security.enable_dns_rebinding_protection)
            self.assertIn("agent.example.org", security.allowed_hosts)
            self.assertIn("https://agent.example.org", security.allowed_origins)
            self.assertNotIn("evil.example.org", security.allowed_hosts)
            with _env(Path(raw) / "tasks.sqlite", WRKHORSE_PUBLIC_HOST="evil.example.org/path"):
                with self.assertRaises(ConfigError):
                    load_gateway_settings()
            with _env(Path(raw) / "tasks.sqlite"):
                gateway = load_gateway_settings()
            self.assertIsNone(_security(gateway))

    def test_mac_bootstrap_script_parses(self):
        proc = subprocess.run(
            ["bash", "-n", str(ROOT / "scripts" / "start-home.sh")],
            text=True, capture_output=True, timeout=5
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
