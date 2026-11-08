"""Process configuration. Gateway and worker settings are separate objects."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from wrkhorse.model_router import load_router, RouterError
from wrkhorse.local_model import (
    DEFAULT_BASE_URL,
    DUMMY_API_KEY,
    LocalModelError,
    validate_base_url,
    validate_model_tag,
)

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
    access_team_domain: str | None
    access_audience: str | None
    model_chat_enabled: bool
    model_chat_model: str | None
    model_chat_timeout_seconds: float
    model_chat_base_url: str
    model_chat_scope: str | None
    model_max_tokens: int
    rate_limit_per_minute: int
    max_body_bytes: int
    stale_seconds: float
    max_attempts: int
    github_drafts_repo: str | None = None
    github_drafts_token: str | None = None


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
    inference_max_tokens: int = 1024
    inference_scope: str | None = None

    @property
    def artifact_root(self) -> Path:
        return self.db_path.parent / "artifacts"


def _shared_limits() -> tuple[float, int]:
    stale = _bounded_float("WRKHORSE_CLAIM_STALE_SECONDS", 900, 1, 86400)
    attempts = _bounded_int("WRKHORSE_MAX_ATTEMPTS", 2, 1, 5)
    return stale, attempts


def _allow_lan() -> bool:
    return _env("WRKHORSE_ALLOW_LAN_MODEL") == "1"


def _max_tokens() -> int:
    # Reasoning models (qwen3, deepseek-r1, gpt-oss) spend tokens "thinking"
    # before the answer; 1024 is often not enough for them.
    return _bounded_int("WRKHORSE_INFERENCE_MAX_TOKENS", 1024, 64, 8192)


def local_model_endpoint(resolve: bool = True) -> tuple[str, str]:
    """The one validated local-model base URL shared by gateway and worker."""
    raw = _env("WRKHORSE_INFERENCE_BASE_URL") or DEFAULT_BASE_URL
    try:
        return validate_base_url(raw, allow_lan=_allow_lan(), resolve=resolve)
    except LocalModelError as exc:
        raise ConfigError(f"WRKHORSE_INFERENCE_BASE_URL: {exc}") from exc


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
    team_domain = _env("WRKHORSE_ACCESS_TEAM_DOMAIN") or None
    audience = _env("WRKHORSE_ACCESS_AUDIENCE") or None
    if bool(team_domain) != bool(audience):
        raise ConfigError("WRKHORSE_ACCESS_TEAM_DOMAIN and WRKHORSE_ACCESS_AUDIENCE must both be set")
    if team_domain is not None:
        # Only pinned, trusted Cloudflare Access JWKS endpoints are permitted.
        if not public_host:
            raise ConfigError("WRKHORSE_PUBLIC_HOST is required with Cloudflare Access")
        if not re.fullmatch(
            r"[a-zA-Z0-9][a-zA-Z0-9.-]{0,180}\.cloudflareaccess\.com",
            team_domain,
        ):
            raise ConfigError("WRKHORSE_ACCESS_TEAM_DOMAIN must be your Cloudflare Access domain")
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", audience or ""):
            raise ConfigError("WRKHORSE_ACCESS_AUDIENCE must be an Access application AUD tag")
    chat_enabled = _env("WRKHORSE_MODEL_CHAT_ENABLED") == "1"
    chat_model = _env("WRKHORSE_INFERENCE_MODEL") or None
    chat_base_url, chat_scope = DEFAULT_BASE_URL, None
    if chat_enabled:
        try:
            validate_model_tag(chat_model)
        except LocalModelError as exc:
            raise ConfigError(f"model chat needs a locally downloaded model tag: {exc}") from exc
        chat_base_url, chat_scope = local_model_endpoint()
        try:
            load_router(local_url=chat_base_url, local_model=chat_model, allow_lan=chat_scope == "lan")
        except RouterError as exc:
            raise ConfigError(f"model router: {exc}") from exc
    github_enabled = _env("WRKHORSE_GITHUB_DRAFTS_ENABLED") == "1"
    draft_repo = _env("WRKHORSE_GITHUB_DRAFT_REPO") or None
    draft_token = os.environ.get("WRKHORSE_GITHUB_TOKEN", "")
    if github_enabled:
        # Always opt in explicitly. This is a GitHub write credential, *not*
        # an Ollama token, MCP bearer token or normal environment default.
        from wrkhorse.github_drafts import DraftError, validate_repo

        try:
            validate_repo(draft_repo or "")
        except DraftError as exc:
            raise ConfigError("WRKHORSE_GITHUB_DRAFT_REPO must be owner/repo") from exc
        if not draft_token or len(draft_token) < 20 or any(
            char.isspace() for char in draft_token
        ):
            raise ConfigError("WRKHORSE_GITHUB_TOKEN requires a fine-grained repository token")
        if draft_token.lower().startswith(("replace-", "changeme")):
            raise ConfigError("WRKHORSE_GITHUB_TOKEN is a placeholder")
    stale, attempts = _shared_limits()
    return GatewaySettings(
        token=token,
        db_path=db_path(),
        host=chosen_host,
        port=chosen_port,
        allow_public_bind=allow_public,
        public_host=public_host,
        access_team_domain=team_domain,
        access_audience=audience,
        model_chat_enabled=chat_enabled,
        model_chat_model=chat_model if chat_enabled else None,
        model_chat_timeout_seconds=_bounded_float(
            "WRKHORSE_MODEL_CHAT_TIMEOUT_SECONDS", 90, 1, 300
        ),
        model_chat_base_url=chat_base_url,
        model_chat_scope=chat_scope,
        model_max_tokens=_max_tokens(),
        rate_limit_per_minute=_bounded_int("WRKHORSE_RATE_LIMIT_PER_MINUTE", 60, 1, 600),
        max_body_bytes=65536,
        stale_seconds=stale,
        max_attempts=attempts,
        github_drafts_repo=draft_repo if github_enabled else None,
        github_drafts_token=draft_token if github_enabled else None,
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
    scope = None
    if mode == "inference-only":
        # Home deployment: the model must run on this machine (loopback) or,
        # with WRKHORSE_ALLOW_LAN_MODEL=1, on another machine the owner
        # controls on a private network. Never a cloud endpoint or cloud tag.
        if not base_url:
            raise ConfigError(
                "inference-only requires WRKHORSE_INFERENCE_BASE_URL, e.g. "
                + DEFAULT_BASE_URL
            )
        base_url, scope = local_model_endpoint()
        try:
            validate_model_tag(model)
        except LocalModelError as exc:
            raise ConfigError(f"inference-only requires a local model tag: {exc}") from exc
        if api_key not in (None, DUMMY_API_KEY):
            raise ConfigError("inference-only refuses a real inference API key")
        api_key = DUMMY_API_KEY  # ignored by Ollama / LM Studio / llama.cpp
        try:
            load_router(local_url=base_url, local_model=model, allow_lan=scope == "lan")
        except RouterError as exc:
            raise ConfigError(f"model router: {exc}") from exc

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
            "WRKHORSE_INFERENCE_TIMEOUT_SECONDS", 60, 1, 300
        ),
        inference_max_tokens=_max_tokens(),
        inference_scope=scope,
    )
