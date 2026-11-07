"""OpenAI-compatible chat call used only by the worker.

The timeout is an end-to-end deadline on a monotonic clock. A slow drip
cannot extend it. Redirects are refused. The gateway token is never sent,
and errors returned to the queue do not include the response body or the key.
"""

from __future__ import annotations

import http.client
import json
import socket
import time
from collections.abc import Callable
from urllib.parse import urlsplit, urlunsplit

MAX_RESPONSE_BYTES = 100_000
_READ_SLICE_SECONDS = 0.25


class InferenceError(Exception):
    """The model call did not produce a usable diff."""


def chat_endpoint(base_url: str) -> str:
    parts = urlsplit(base_url.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise InferenceError("inference URL must be http or https")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise InferenceError("inference URL must not carry credentials or a query")
    path = parts.path.rstrip("/")
    if path.endswith("/chat/completions"):
        full = path
    elif path.endswith("/v1"):
        full = path + "/chat/completions"
    else:
        raise InferenceError("inference URL must end in /v1")
    return urlunsplit((parts.scheme, parts.netloc, full, "", ""))


def request_diff(
    *,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    timeout: float,
    should_stop: Callable[[], bool] | None = None,
) -> str:
    if not api_key or not model:
        raise InferenceError("inference is not configured")
    if timeout <= 0:
        raise InferenceError("inference deadline exceeded")
    endpoint = chat_endpoint(base_url)
    deadline = time.monotonic() + timeout
    body = json.dumps(
        {
            "model": model,
            "temperature": 0,
            "max_tokens": 1024,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Reply with one unified diff and no other text. "
                        "Do not include shell commands."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        }
    ).encode("utf-8")
    parts = urlsplit(endpoint)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    conn: http.client.HTTPConnection | None = None
    try:
        conn = _connect(parts.scheme, parts.hostname or "", port, deadline, should_stop)
        _check(deadline, should_stop)
        conn.request(
            "POST",
            parts.path or "/",
            body=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "Connection": "close",
            },
        )
        _arm(conn, deadline, should_stop)
        response = conn.getresponse()
        if response.status in {301, 302, 303, 307, 308}:
            raise InferenceError("inference redirects are refused")
        raw = _read_bounded(response, conn, deadline, should_stop)
        if response.status != 200:
            raise InferenceError("inference request failed")
    except InferenceError:
        raise
    except Exception as exc:
        raise InferenceError("inference request failed") from exc
    finally:
        if conn is not None:
            conn.close()
    if len(raw) > MAX_RESPONSE_BYTES:
        raise InferenceError("inference response too large")
    try:
        payload = json.loads(raw.decode("utf-8"))
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError, UnicodeError) as exc:
        raise InferenceError("inference response was not a chat completion") from exc
    if not isinstance(content, str) or not content.strip():
        raise InferenceError("inference response was empty")
    return content


def _connect(scheme, host, port, deadline, should_stop):
    _check(deadline, should_stop)
    if scheme == "https":
        conn = http.client.HTTPSConnection(host, port, timeout=_remaining(deadline))
    else:
        conn = http.client.HTTPConnection(host, port, timeout=_remaining(deadline))
    try:
        conn.connect()
    except (TimeoutError, socket.timeout) as exc:
        conn.close()
        if time.monotonic() >= deadline:
            raise InferenceError("inference deadline exceeded") from exc
        raise InferenceError("inference request failed") from exc
    return conn


def _read_bounded(response, conn, deadline, should_stop) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total <= MAX_RESPONSE_BYTES:
        _arm(conn, deadline, should_stop)
        try:
            block = response.read(4096)
        except (TimeoutError, socket.timeout, OSError) as exc:
            if isinstance(exc, OSError) and "timed out" not in str(exc).lower():
                raise
            raw = getattr(getattr(response, "fp", None), "raw", None)
            if raw is not None:
                raw._timeout_occurred = False
            continue
        if not block:
            break
        total += len(block)
        if total > MAX_RESPONSE_BYTES:
            raise InferenceError("inference response too large")
        chunks.append(block)
    return b"".join(chunks)


def _arm(conn, deadline, should_stop) -> None:
    _check(deadline, should_stop)
    sock = getattr(conn, "sock", None)
    if sock is not None:
        sock.settimeout(min(_READ_SLICE_SECONDS, _remaining(deadline)))


def _check(deadline: float, should_stop: Callable[[], bool] | None) -> None:
    if should_stop is not None and should_stop():
        raise InferenceError("inference lease lost")
    if time.monotonic() >= deadline:
        raise InferenceError("inference deadline exceeded")


def _remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise InferenceError("inference deadline exceeded")
    return left
