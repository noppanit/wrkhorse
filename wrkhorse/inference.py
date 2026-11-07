"""OpenAI-compatible chat call used only by the worker.

One asyncio.wait_for deadline covers connect, headers, and body. Cancelling
that task closes the client and the response. The request asks for an
identity body. Any other Content-Encoding is refused before the body is
read. An identity body is capped at 100,000 bytes. A fast drip cannot extend
the deadline by keeping the socket busy. Redirects are refused. Errors
returned to the queue do not include the response body or the key.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from urllib.parse import urlsplit, urlunsplit

import httpx2

MAX_RESPONSE_BYTES = 100_000
_REDIRECTS = {301, 302, 303, 307, 308}
_REQUEST_HEADERS = {
    "Accept": "application/json",
    "Accept-Encoding": "identity",
}


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
    body = {
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
    try:
        return asyncio.run(_bounded(endpoint, api_key, body, timeout, should_stop))
    except TimeoutError as exc:
        raise InferenceError("inference deadline exceeded") from exc


async def _bounded(endpoint, api_key, body, timeout, should_stop) -> str:
    task = asyncio.create_task(_exchange(endpoint, api_key, body))
    try:
        return await asyncio.wait_for(_finish_or_stop(task, should_stop), timeout)
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


async def _finish_or_stop(task: asyncio.Task[str], should_stop) -> str:
    while not task.done():
        if should_stop is not None and should_stop():
            task.cancel()
            raise InferenceError("inference lease lost")
        await asyncio.wait({task}, timeout=0.05)
    return task.result()


async def _exchange(endpoint: str, api_key: str, body: dict) -> str:
    # No client-side read timeout. The outer wait_for is the only deadline.
    # Identity only: a compressed body is refused from the headers, before it is read.
    client = httpx2.AsyncClient(
        timeout=None,
        follow_redirects=False,
        trust_env=False,
        headers=_REQUEST_HEADERS,
    )
    try:
        async with client.stream(
            "POST",
            endpoint,
            json=body,
            headers={**_REQUEST_HEADERS, "Authorization": f"Bearer {api_key}"},
        ) as response:
            if response.status_code in _REDIRECTS:
                raise InferenceError("inference redirects are refused")
            if response.status_code != 200:
                raise InferenceError("inference request failed")
            _require_identity(response)
            raw = await _read_bounded(response)
            return _parse_content(raw)
    finally:
        await client.aclose()


def _require_identity(response) -> None:
    values = response.headers.get_list("content-encoding", split_commas=True)
    for value in values:
        if value.strip().lower() != "identity":
            raise InferenceError("inference response failed")


async def _read_bounded(response) -> bytes:
    """Count raw identity bytes and close as soon as they pass the limit."""
    parts: list[bytes] = []
    total = 0
    async for chunk in response.aiter_raw():
        if not chunk:
            continue
        room = MAX_RESPONSE_BYTES - total
        if len(chunk) > room:
            raise InferenceError("inference response too large")
        parts.append(chunk)
        total += len(chunk)
    return b"".join(parts)


def _parse_content(raw: bytes) -> str:
    try:
        payload = json.loads(raw.decode("utf-8"))
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError, UnicodeError) as exc:
        raise InferenceError("inference response was not a chat completion") from exc
    if not isinstance(content, str) or not content.strip():
        raise InferenceError("inference response was empty")
    return content
