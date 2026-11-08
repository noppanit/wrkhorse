"""Offline router policy tests; no GPU, tunnel, or paid services required."""
import os
import unittest
from unittest.mock import patch
from wrkhorse.model_router import load_router, RouterError

LOCAL = "http://127.0.0.1:11434/v1"
CLOUD = "http://127.0.0.1:11435/v1"

class ModelRouterTests(unittest.TestCase):
    def load(self, **env):
        with patch.dict(os.environ, env, clear=True):
            return load_router(local_url=LOCAL, local_model="qwen2.5-coder:1.5b")

    def test_default_auto_never_uses_cloud(self):
        router = self.load()
        self.assertEqual(router.mode, "auto")
        self.assertEqual([r.name for r in router.candidates("chat")], ["local"])
        self.assertEqual([r.name for r in router.candidates("coding")], ["local"])

    def test_explicit_cloud_auto_routes_coding_only(self):
        router = self.load(WRKHORSE_CLOUD_INFERENCE_ENABLED="1",
                           WRKHORSE_CLOUD_INFERENCE_BASE_URL=CLOUD,
                           WRKHORSE_CLOUD_INFERENCE_MODEL="qwen3-coder:30b")
        self.assertEqual([r.name for r in router.candidates("chat")], ["local"])
        self.assertEqual([r.name for r in router.candidates("coding")], ["cloud-tunnel", "local"])

    def test_local_mode_never_rents_gpu(self):
        router = self.load(WRKHORSE_MODEL_ROUTING="local",
                           WRKHORSE_CLOUD_INFERENCE_ENABLED="1",
                           WRKHORSE_CLOUD_INFERENCE_BASE_URL=CLOUD,
                           WRKHORSE_CLOUD_INFERENCE_MODEL="qwen3-coder:30b")
        self.assertEqual([r.name for r in router.candidates("coding")], ["local"])

    def test_cloud_mode_requires_opt_in(self):
        with self.assertRaises(RouterError):
            self.load(WRKHORSE_MODEL_ROUTING="cloud")
        router = self.load(WRKHORSE_MODEL_ROUTING="cloud",
                           WRKHORSE_CLOUD_INFERENCE_ENABLED="1",
                           WRKHORSE_CLOUD_INFERENCE_BASE_URL=CLOUD,
                           WRKHORSE_CLOUD_INFERENCE_MODEL="qwen3-coder:30b")
        self.assertEqual([r.name for r in router.candidates("chat")], ["cloud-tunnel"])

    def test_refuse_public_endpoints_and_ollama_cloud_tags(self):
        for url in ("https://api.runpod.ai/v1", "http://169.254.169.254/v1",
                    "http://192.168.1.2:11434/v1"):
            with self.subTest(url=url), self.assertRaises(RouterError):
                self.load(WRKHORSE_CLOUD_INFERENCE_ENABLED="1",
                          WRKHORSE_CLOUD_INFERENCE_BASE_URL=url,
                          WRKHORSE_CLOUD_INFERENCE_MODEL="qwen3-coder:30b")
        with self.assertRaises(RouterError):
            self.load(WRKHORSE_CLOUD_INFERENCE_ENABLED="1",
                      WRKHORSE_CLOUD_INFERENCE_BASE_URL=CLOUD,
                      WRKHORSE_CLOUD_INFERENCE_MODEL="qwen3-coder:cloud")

    def test_reject_implicit_enablement_and_same_endpoint(self):
        with self.assertRaises(RouterError):
            self.load(WRKHORSE_CLOUD_INFERENCE_BASE_URL=CLOUD)
        with self.assertRaises(RouterError):
            self.load(WRKHORSE_CLOUD_INFERENCE_ENABLED="1",
                      WRKHORSE_CLOUD_INFERENCE_BASE_URL=LOCAL,
                      WRKHORSE_CLOUD_INFERENCE_MODEL="qwen3-coder:30b")

    def test_unknown_mode_and_task(self):
        with self.assertRaises(RouterError):
            self.load(WRKHORSE_MODEL_ROUTING="random")
        with self.assertRaises(RouterError):
            self.load().candidates("deploy")

if __name__ == "__main__":
    unittest.main()
