"""OpenAI-compatible chat call used only by the worker.

One asyncio.wait_for deadline covers connect, headers, and body. Cancelling
that task closes the client and the response. A fast drip cannot extend the
deadline by keeping the socket busy. Redirects are refused. Errors returned
to the queue do not include the response body or the key.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from urllib.parse import urlsplit, urlunsplit

import httpx2

MAX_RESPONSE_BYTES = 100_000
_REDIRECTS = {301, 302, 303, 307, 308}


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
    client = httpx2.AsyncClient(timeout=None, follow_redirects=False, trust_env=False)
    response = None
    try:
        response = await client.post(
            endpoint,
            json=body,
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        )
        if response.status_code in _REDIRECTS:
            raise InferenceError("inference redirects are refused")
        if response.status_code != 200:
            raise InferenceError("inference request failed")
        raw = await response.aread()
        if len(raw) > MAX_RESPONSE_BYTES:
            raise InferenceError("inference response too large")
        return _parse_content(raw)
    finally:
        if response is not None:
            await response.aclose()
        await client.aclose()


def _parse_content(raw: bytes) -> str:
    try:
        payload = json.loads(raw.decode("utf-8"))
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError, UnicodeError) as exc:
        raise InferenceError("inference response was not a chat completion") from exc
    if not isinstance(content, str) or not content.strip():
        raise InferenceError("inference response was empty")
    return content
