"""Opt-in inference routing; cloud means an owner-managed LOOPBACK SSH tunnel.

No public GPU URL, credentials, GPU lifecycle API, or automatic provisioning.
The existing local endpoint policy remains the sole endpoint validator.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from wrkhorse.local_model import DEFAULT_BASE_URL, LocalModelError, validate_base_url, validate_model_tag

class RouterError(ValueError):
    pass

@dataclass(frozen=True)
class Route:
    name: str
    base_url: str
    model: str

@dataclass(frozen=True)
class ModelRouter:
    mode: str
    local: Route
    cloud: Route | None

    def candidates(self, task: str) -> tuple[Route, ...]:
        if task not in {"chat", "coding"}:
            raise RouterError("unknown task class")
        if self.mode == "local":
            return (self.local,)
        if self.mode == "cloud":
            if self.cloud is None:
                raise RouterError("cloud route not enabled")
            return (self.cloud,)
        # Auto: keep conversational requests local; heavy coding uses the
        # explicitly enabled cloud route first, then local on failure.
        if task == "coding" and self.cloud is not None:
            return (self.cloud, self.local)
        return (self.local,)

def load_router(*, local_url: str = DEFAULT_BASE_URL, local_model: str | None = None,
                allow_lan: bool = False) -> ModelRouter:
    mode = os.environ.get("WRKHORSE_MODEL_ROUTING", "auto").strip()
    if mode not in {"auto", "local", "cloud"}:
        raise RouterError("WRKHORSE_MODEL_ROUTING must be auto, local or cloud")
    try:
        base, _ = validate_base_url(local_url, allow_lan=allow_lan)
        model = validate_model_tag(local_model)
    except LocalModelError as exc:
        raise RouterError(str(exc)) from exc
    cloud = None
    enabled = os.environ.get("WRKHORSE_CLOUD_INFERENCE_ENABLED", "0") == "1"
    cloud_url = os.environ.get("WRKHORSE_CLOUD_INFERENCE_BASE_URL", "").strip()
    cloud_model = os.environ.get("WRKHORSE_CLOUD_INFERENCE_MODEL", "").strip()
    if enabled:
        if not cloud_url or not cloud_model:
            raise RouterError("cloud inference requires a tunnel URL and model")
        try:
            url, scope = validate_base_url(cloud_url, allow_lan=False)
            tag = validate_model_tag(cloud_model)
        except LocalModelError as exc:
            raise RouterError(f"cloud tunnel: {exc}") from exc
        if scope != "loopback":
            raise RouterError("cloud inference must use a loopback SSH tunnel")
        if url == base:
            raise RouterError("cloud and local inference must use different endpoints")
        cloud = Route("cloud-tunnel", url, tag)
    elif cloud_url or cloud_model:
        raise RouterError("cloud endpoint/model set without WRKHORSE_CLOUD_INFERENCE_ENABLED=1")
    if mode == "cloud" and cloud is None:
        raise RouterError("cloud mode requires explicit cloud inference enablement")
    return ModelRouter(mode, Route("local", base, model), cloud)
