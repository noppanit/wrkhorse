"""No-network tests for installed-user setup."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wrkhorse.installer import init, _environment, start

class InstalledTests(unittest.TestCase):
    def test_init_is_idempotent_and_private(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"WRKHORSE_HOME": tmp}, clear=True):
            self.assertEqual(init(), 0)
            token = (Path(tmp) / "mcp-token").read_text()
            self.assertGreaterEqual(len(token.strip()), 32)
            self.assertEqual(init(model="llama3.2"), 0)
            self.assertEqual((Path(tmp) / "mcp-token").read_text(), token)
            self.assertEqual(json.loads((Path(tmp) / "config.json").read_text())["model"], "qwen2.5-coder:1.5b")
            self.assertEqual((Path(tmp) / "mcp-token").stat().st_mode & 0o777, 0o600)

    def test_runtime_forces_loopback_and_clears_cloud(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {
                "WRKHORSE_HOME": tmp,
                "WRKHORSE_HOST": "0.0.0.0",
                "WRKHORSE_ALLOW_PUBLIC_BIND": "1",
                "WRKHORSE_CLOUD_INFERENCE_ENABLED": "1",
                "WRKHORSE_CLOUD_INFERENCE_BASE_URL": "http://127.0.0.1:11435/v1",
                "WRKHORSE_CLOUD_INFERENCE_MODEL": "qwen3-coder:30b",
            }, clear=True):
                init()
                env = _environment()
                self.assertEqual(env["WRKHORSE_HOST"], "127.0.0.1")
                self.assertEqual(env["WRKHORSE_MODEL_ROUTING"], "auto")
                self.assertNotIn("WRKHORSE_CLOUD_INFERENCE_ENABLED", env)
                self.assertNotIn("WRKHORSE_ALLOW_PUBLIC_BIND", env)
                self.assertEqual(env["WRKHORSE_EXECUTION_MODE"], "inference-only")

    def test_check_does_not_start_worker_or_gateway(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"WRKHORSE_HOME": tmp}, clear=True):
            init()
            with patch("wrkhorse.installer.subprocess.run") as probe, patch("wrkhorse.installer.subprocess.Popen") as worker:
                probe.return_value.returncode = 0
                self.assertEqual(start(check=True), 0)
                worker.assert_not_called()

    def test_missing_init_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"WRKHORSE_HOME": tmp}, clear=True):
            with self.assertRaises(RuntimeError):
                _environment()

if __name__ == "__main__":
    unittest.main()
