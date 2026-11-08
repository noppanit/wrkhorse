"""Installed Mac entry point: explicit, local-only setup and supervised runtime.

No sudo, automatic downloads, GPU provisioning, tunnel creation or deployment.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_MODEL = "qwen2.5-coder:1.5b"
DEFAULT_URL = "http://127.0.0.1:11434/v1"

def home() -> Path:
    return Path(os.environ.get("WRKHORSE_HOME", str(Path.home() / ".local" / "share" / "wrkhorse"))).expanduser()

def _private_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise RuntimeError(f"refusing symlink: {path}")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(data)

def init(*, model: str = DEFAULT_MODEL) -> int:
    from wrkhorse.local_model import validate_model_tag
    validate_model_tag(model)
    root = home()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    config = root / "config.json"
    token = root / "mcp-token"
    if not config.exists():
        _private_write(config, json.dumps({"model": model, "base_url": DEFAULT_URL}, indent=2) + "\n")
        print(f"Created {config}")
    else:
        print(f"Keeping existing {config}")
    if not token.exists():
        _private_write(token, secrets.token_urlsafe(48) + "\n")
        print("Generated a private MCP token (not printed).")
    else:
        print("Keeping existing MCP token.")
    if shutil.which("ollama") is None:
        print("Next: install Ollama from https://ollama.com/download/mac")
    else:
        print(f"Next: ollama pull {model}  (downloads model only with your approval)")
    print("Then: wrkhorse start")
    print("Remote Grok MCP access requires separate authenticated Cloudflare configuration.")
    return 0

def _environment() -> dict[str, str]:
    root = home()
    config = root / "config.json"
    token = root / "mcp-token"
    if not config.is_file() or not token.is_file() or config.is_symlink() or token.is_symlink():
        raise RuntimeError("run 'wrkhorse init' first")
    settings = json.loads(config.read_text(encoding="utf-8"))
    if not isinstance(settings, dict) or set(settings) != {"model", "base_url"}:
        raise RuntimeError("invalid wrkhorse config")
    from wrkhorse.local_model import validate_base_url, validate_model_tag
    validate_model_tag(settings["model"])
    validate_base_url(settings["base_url"], allow_lan=False)
    secret = token.read_text(encoding="utf-8").strip()
    from wrkhorse.config import validate_token
    validate_token(secret)
    env = os.environ.copy()
    defaults = {
        "WRKHORSE_AUTH_TOKEN": secret,
        "WRKHORSE_DB": str(root / "jobs.sqlite"),
        "WRKHORSE_INFERENCE_MODEL": settings["model"],
        "WRKHORSE_INFERENCE_BASE_URL": settings["base_url"],
        "WRKHORSE_INFERENCE_API_KEY": "ollama-local",
        "WRKHORSE_EXECUTION_MODE": "inference-only",
        "WRKHORSE_MODEL_CHAT_ENABLED": "1",
        "WRKHORSE_HOST": "127.0.0.1",
        "WRKHORSE_PORT": "8765",
        "WRKHORSE_MODEL_ROUTING": "auto",
    }
    # Never inherit a public bind or a remote model from ambient variables.
    env.update(defaults)
    env.pop("WRKHORSE_ALLOW_PUBLIC_BIND", None)
    env.pop("WRKHORSE_ALLOW_LAN_MODEL", None)
    return env

def start(*, check: bool = False) -> int:
    env = _environment()
    # Check before spawning processes; no GPU use and no model warm-up.
    probe = subprocess.run([sys.executable, "-m", "wrkhorse", "doctor"], env=env, check=False)
    if probe.returncode:
        print(f"Model not ready. Run: ollama pull {env['WRKHORSE_INFERENCE_MODEL']}", file=sys.stderr)
        return probe.returncode
    if check:
        print("Wrkhorse preflight passed. No server started.")
        return 0
    worker_env = env.copy()
    for name in ("WRKHORSE_GITHUB_TOKEN", "WRKHORSE_GITHUB_DRAFT_REPO", "WRKHORSE_GITHUB_DRAFTS_ENABLED"):
        worker_env.pop(name, None)
    worker = subprocess.Popen([sys.executable, "-m", "wrkhorse", "worker"], env=worker_env)
    try:
        print("Wrkhorse running on http://127.0.0.1:8765/mcp (Ctrl-C to stop)", flush=True)
        return subprocess.call([sys.executable, "-m", "wrkhorse", "serve"], env=env)
    finally:
        worker.terminate()
        try:
            worker.wait(timeout=5)
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.wait()
