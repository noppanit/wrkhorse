"""MCP gateway. It queues and reads. It does not call a model or run code."""

from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

from wrkhorse.auth import RateLimiter, bearer_matches
from wrkhorse.config import GatewaySettings
from wrkhorse.store import Store, public_result, public_status

_JOB_ID = re.compile(r"^[0-9a-f]{32}$")


def build_server(settings: GatewaySettings) -> MCPServer:
    store = Store(
        settings.db_path,
        stale_seconds=settings.stale_seconds,
        max_attempts=settings.max_attempts,
    )
    mcp = MCPServer(
        "wrkhorse",
        instructions=(
            "Queue and inspect one registered coding job, sample-add. "
            "Do not send repository paths, shell commands, or credentials. "
            "submit_task only stores a job. A separate worker claims it. "
            "The default worker refuses code execution. Model-written code is never executed. "
            "Research tasks are not implemented."
        ),
    )

    @mcp.tool()
    def submit_task(task_name: str, mode: str = "mock") -> dict:
        """Queue sample-add. mode is mock or inference. No path or shell argument exists."""
        if task_name != "sample-add" or mode not in {"mock", "inference"}:
            raise ToolError(
                "unknown task or mode; the only task is sample-add and mode is mock or inference"
            )
        job = store.enqueue(task_name, mode)
        return public_status(job)

    @mcp.tool()
    def get_task_status(job_id: str) -> dict:
        """Return status for a job id from submit_task. Does not run anything."""
        job = _load(store, job_id)
        return public_status(job)

    @mcp.tool()
    def get_task_result(job_id: str) -> dict:
        """Return the stored result, including a diff when one exists. Does not run anything."""
        job = _load(store, job_id)
        return public_result(job)

    @mcp.tool()
    def get_project_status() -> dict:
        """Return pilot readiness. Does not run a task."""
        return store.project_status()

    return mcp


def _load(store: Store, job_id: str) -> dict:
    if _JOB_ID.fullmatch(job_id) is None:
        raise ToolError("unknown job")
    job = store.get(job_id)
    if job is None:
        raise ToolError("unknown job")
    return job


def _send_json(send, status: int, payload: dict, extra_headers: list[tuple[bytes, bytes]] | None = None):
    body = json.dumps(payload).encode("utf-8")
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
        (b"cache-control", b"no-store"),
    ]
    if extra_headers:
        headers.extend(extra_headers)

    async def _send() -> None:
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    return _send()


class _Guard:
    def __init__(self, app, token: str, limiter: RateLimiter) -> None:
        self.app = app
        self.token = token
        self.limiter = limiter

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        client = scope.get("client") or ("unknown", 0)
        if not self.limiter.allow(str(client[0])):
            await _send_json(send, 429, {"error": "rate_limited"})
            return
        header = _header(scope, "authorization")
        if not bearer_matches(header, self.token):
            await _send_json(
                send,
                401,
                {"error": "unauthorized"},
                [(b"www-authenticate", b"Bearer")],
            )
            return
        await self.app(scope, receive, send)


def _header(scope, name: str) -> str:
    for key, value in scope.get("headers") or []:
        if key.decode("latin1").lower() == name:
            return value.decode("latin1")
    return ""


def _security(settings: GatewaySettings) -> TransportSecuritySettings | None:
    # Named tunnels forward to a loopback-bound gateway but preserve the
    # external Host. The MCP SDK's default localhost-only allowlist would
    # otherwise reject those requests.
    if not settings.public_host and settings.host in {"127.0.0.1", "localhost", "::1"}:
        return None
    host = settings.public_host or ""
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[host, f"{host}:443", "127.0.0.1:*", "localhost:*"],
        allowed_origins=[f"https://{host}"],
    )


def http_app(settings: GatewaySettings):
    server = build_server(settings)
    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        max_request_body_size=settings.max_body_bytes,
        host=settings.host,
        transport_security=_security(settings),
    )
    return _Guard(
        app,
        settings.token,
        RateLimiter(settings.rate_limit_per_minute, 60),
    )


def serve_http(settings: GatewaySettings) -> None:
    uvicorn.run(
        http_app(settings),
        host=settings.host,
        port=settings.port,
        access_log=False,
        log_level="warning",
    )


def serve_stdio(settings: GatewaySettings) -> None:
    build_server(settings).run(transport="stdio")


def public_mcp_url(settings: GatewaySettings) -> str:
    if settings.public_host:
        base = f"https://{settings.public_host}"
    else:
        base = f"http://{settings.host}:{settings.port}"
    parts = urlsplit(base)
    return f"{parts.scheme}://{parts.netloc}/mcp"
