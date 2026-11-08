"""Process configuration. Gateway and worker settings are separate objects."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

MIN_TOKEN_LENGTH = 32
_PLACEHOLDER_PREFIXES = ("replace-with", "changeme")
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class ConfigError(Exception):
    """The process must stop before listening, queueing, or calling a model."""


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _bounded_int(name: str, default: int, low: int, high: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if value < low or value > high:
        raise ConfigError(f"{name} must be between {low} and {high}")
    return value


def _bounded_float(name: str, default: float, low: float, high: float) -> float:
    raw = _env(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number") from exc
    if value < low or value > high:
        raise ConfigError(f"{name} must be between {low} and {high}")
    return value


def validate_token(token: str) -> str:
    if len(token) < MIN_TOKEN_LENGTH or any(char.isspace() for char in token):
        raise ConfigError(
            "WRKHORSE_AUTH_TOKEN must be a single token of at least "
            f"{MIN_TOKEN_LENGTH} characters"
        )
    lowered = token.lower()
    if any(lowered.startswith(prefix) for prefix in _PLACEHOLDER_PREFIXES):
        raise ConfigError("WRKHORSE_AUTH_TOKEN is still a placeholder")
    return token


def require_token() -> str:
    token = os.environ.get("WRKHORSE_AUTH_TOKEN", "")
    if token.strip() != token:
        raise ConfigError("WRKHORSE_AUTH_TOKEN must not begin or end with whitespace")
    if not token:
        raise ConfigError("WRKHORSE_AUTH_TOKEN is required")
    return validate_token(token)


def db_path() -> Path:
    raw = _env("WRKHORSE_DB") or "var/wrkhorse.sqlite"
    path = Path(raw)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


@dataclass(frozen=True)
class GatewaySettings:
    token: str
    db_path: Path
    host: str
    port: int
    allow_public_bind: bool
    public_host: str | None
    rate_limit_per_minute: int
    max_body_bytes: int
    stale_seconds: float
    max_attempts: int


@dataclass(frozen=True)
class WorkerSettings:
    db_path: Path
    execution_mode: str
    stale_seconds: float
    max_attempts: int
    poll_seconds: float
    test_timeout_seconds: float
    max_diff_bytes: int
    inference_base_url: str | None
    inference_api_key: str | None
    inference_model: str | None
    inference_timeout_seconds: float

    @property
    def artifact_root(self) -> Path:
        return self.db_path.parent / "artifacts"


def _shared_limits() -> tuple[float, int]:
    stale = _bounded_float("WRKHORSE_CLAIM_STALE_SECONDS", 900, 1, 86400)
    attempts = _bounded_int("WRKHORSE_MAX_ATTEMPTS", 2, 1, 5)
    return stale, attempts


def load_gateway_settings(host: str | None = None, port: int | None = None) -> GatewaySettings:
    """Load gateway settings. This function does not read the inference key."""
    token = require_token()
    chosen_host = host if host is not None else (_env("WRKHORSE_HOST") or "127.0.0.1")
    chosen_port = port if port is not None else _bounded_int("WRKHORSE_PORT", 8765, 1, 65535)
    allow_public = _env("WRKHORSE_ALLOW_PUBLIC_BIND") == "1"
    public_host = _env("WRKHORSE_PUBLIC_HOST") or None
    if chosen_host not in _LOOPBACK and not allow_public:
        raise ConfigError(
            "refusing non-loopback bind without WRKHORSE_ALLOW_PUBLIC_BIND=1"
        )
    if chosen_host not in _LOOPBACK and not public_host:
        raise ConfigError(
            "WRKHORSE_PUBLIC_HOST is required when the gateway is not on loopback"
        )
    # A tunnel can forward to a loopback-bound server while preserving the
    # public Host. Never accept userinfo, wildcards, schemes or paths.
    if public_host and not re.fullmatch(
        r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]{0,251}[a-zA-Z0-9])?", public_host
    ):
        raise ConfigError("WRKHORSE_PUBLIC_HOST must be a bare DNS hostname")
    stale, attempts = _shared_limits()
    return GatewaySettings(
        token=token,
        db_path=db_path(),
        host=chosen_host,
        port=chosen_port,
        allow_public_bind=allow_public,
        public_host=public_host,
        rate_limit_per_minute=_bounded_int("WRKHORSE_RATE_LIMIT_PER_MINUTE", 60, 1, 600),
        max_body_bytes=65536,
        stale_seconds=stale,
        max_attempts=attempts,
    )


def load_worker_settings() -> WorkerSettings:
    """Load worker settings. This function does not read the gateway token."""
    mode = _env("WRKHORSE_EXECUTION_MODE") or "disabled"
    if mode not in {"disabled", "trusted-local-demo", "inference-only"}:
        raise ConfigError(
            "WRKHORSE_EXECUTION_MODE must be disabled, trusted-local-demo or inference-only"
        )
    base_url = _env("WRKHORSE_INFERENCE_BASE_URL") or None
    api_key = os.environ.get("WRKHORSE_INFERENCE_API_KEY")
    if api_key is not None and api_key.strip() != api_key:
        raise ConfigError("WRKHORSE_INFERENCE_API_KEY must not contain surrounding whitespace")
    api_key = api_key or None
    model = _env("WRKHORSE_INFERENCE_MODEL") or None
    if model is not None and len(model) > 200:
        raise ConfigError("WRKHORSE_INFERENCE_MODEL is too long")
    if mode == "inference-only":
        # This deployment mode is intended for a Mac that runs its own
        # Ollama. Reject all remote inference destinations and :cloud tags.
        if base_url != "http://127.0.0.1:11434/v1":
            raise ConfigError(
                "inference-only requires WRKHORSE_INFERENCE_BASE_URL="
                "http://127.0.0.1:11434/v1"
            )
        if not model or model.endswith(":cloud"):
            raise ConfigError("inference-only requires a local Ollama model tag")
        if api_key not in (None, "ollama-local"):
            raise ConfigError("inference-only refuses a real inference API key")
        api_key = "ollama-local"  # ignored by local Ollama OpenAI compatibility

    stale, attempts = _shared_limits()
    return WorkerSettings(
        db_path=db_path(),
        execution_mode=mode,
        stale_seconds=stale,
        max_attempts=attempts,
        poll_seconds=_bounded_float("WRKHORSE_POLL_SECONDS", 0.5, 0.05, 30),
        test_timeout_seconds=_bounded_float("WRKHORSE_TEST_TIMEOUT_SECONDS", 20, 1, 60),
        max_diff_bytes=_bounded_int("WRKHORSE_MAX_DIFF_BYTES", 32768, 100, 100000),
        inference_base_url=base_url,
        inference_api_key=api_key,
        inference_model=model,
        inference_timeout_seconds=_bounded_float(
            "WRKHORSE_INFERENCE_TIMEOUT_SECONDS", 60, 1, 120
        ),
    )
