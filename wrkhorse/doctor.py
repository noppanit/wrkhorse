"""Preflight for the local model: reachable, model present, optionally warm.

Works against any OpenAI-compatible local server (Ollama, LM Studio,
llama.cpp, mlx_lm.server, vLLM) on this machine or, with
WRKHORSE_ALLOW_LAN_MODEL=1, on another machine on the private network. It
uses the same endpoint policy as the gateway and worker, so a URL that passes
here is the URL they will use. It never prints a token.
"""

from __future__ import annotations

import time

import httpx2

from wrkhorse.config import ConfigError, _env, local_model_endpoint
from wrkhorse.inference import InferenceEmptyResponse, InferenceError, request_diff
from wrkhorse.local_model import DUMMY_API_KEY, LocalModelError, validate_model_tag


def model_listed(model: str, ids: list[str]) -> bool:
    candidates = {model}
    if model.endswith(":latest"):
        candidates.add(model[: -len(":latest")])
    elif ":" not in model:
        candidates.add(model + ":latest")
    return any(item in candidates for item in ids)


def run_doctor(*, warm: bool, out=print) -> int:
    try:
        base_url, scope = local_model_endpoint()
        model = validate_model_tag(_env("WRKHORSE_INFERENCE_MODEL") or "qwen2.5-coder:1.5b")
    except (ConfigError, LocalModelError) as exc:
        out(f"FAIL config: {exc}")
        return 2
    out(f"model endpoint: {base_url} ({scope})")
    out(f"model: {model}")
    try:
        with httpx2.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
            response = client.get(
                base_url + "/models",
                headers={"Authorization": f"Bearer {DUMMY_API_KEY}", "Accept": "application/json"},
            )
    except (httpx2.HTTPError, OSError) as exc:
        out(f"FAIL cannot reach the model server ({type(exc).__name__}).")
        if scope == "loopback":
            out("  Start Ollama (open the Ollama app) or your local model server first.")
        else:
            out("  Is the other machine awake and is its server listening on the LAN/tailnet?")
            out("  For Ollama there: OLLAMA_HOST=0.0.0.0:11434 (and firewall it to your LAN).")
        return 2
    if response.status_code != 200:
        out(f"FAIL {base_url}/models answered HTTP {response.status_code}; not an OpenAI-compatible server?")
        return 2
    try:
        ids = [str(item.get("id")) for item in response.json().get("data", [])]
    except (ValueError, AttributeError):
        out("FAIL model list was not OpenAI-compatible JSON")
        return 2
    if not model_listed(model, ids):
        out(f"FAIL model {model!r} is not available on that server.")
        out(f"  Ollama: run `ollama pull {model}` on the machine that serves it.")
        if ids:
            out("  Available: " + ", ".join(sorted(ids)[:20]))
        return 2
    out("OK model server reachable and model present")
    if not warm:
        return 0
    # The first request loads weights into memory, which can take minutes for
    # larger models on a MacBook Air. Do it now, not inside a client's deadline.
    started = time.monotonic()
    try:
        request_diff(
            base_url=base_url,
            api_key=DUMMY_API_KEY,
            model=model,
            prompt="Reply with the single word: ready",
            timeout=300,
            max_tokens=256,
        )
    except InferenceEmptyResponse:
        pass  # an empty or truncated reply still proves the model loaded and answered
    except InferenceError as exc:
        out(f"FAIL warm-up: {exc}")
        return 2
    out(f"OK model loaded and answered in {time.monotonic() - started:.1f}s")
    return 0

