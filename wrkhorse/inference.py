"""OpenAI-compatible chat call used only by the worker.

One asyncio.wait_for deadline covers connect, headers, and body. Cancelling
that task closes the client and the response. A fast drip cannot extend the
deadline by keeping the socket busy. Redirects are refused. Errors returned
to the queue do not include the response body or the key.
"""

from __future__ import annotations

import asyncio
import json
import zlib
from collections.abc import Callable
from urllib.parse import urlsplit, urlunsplit

import httpx2

MAX_RESPONSE_BYTES = 100_000
_REDIRECTS = {301, 302, 303, 307, 308}
_SUPPORTED_ENCODINGS = {"gzip", "x-gzip", "deflate"}


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
    try:
        async with client.stream(
            "POST",
            endpoint,
            json=body,
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        ) as response:
            if response.status_code in _REDIRECTS:
                raise InferenceError("inference redirects are refused")
            if response.status_code != 200:
                raise InferenceError("inference request failed")
            raw = await _read_bounded(response)
            return _parse_content(raw)
    finally:
        await client.aclose()


async def _read_bounded(response) -> bytes:
    """Count decoded bytes and close as soon as they pass the limit."""
    decoder = _Decoder(_content_encoding(response))
    parts: list[bytes] = []
    total = 0
    async for chunk in response.aiter_raw():
        if not chunk:
            continue
        piece, overflow = decoder.feed(chunk, MAX_RESPONSE_BYTES - total)
        if piece:
            parts.append(piece)
            total += len(piece)
        if overflow or total > MAX_RESPONSE_BYTES:
            raise InferenceError("inference response too large")
    piece, overflow = decoder.finish(MAX_RESPONSE_BYTES - total)
    if piece:
        parts.append(piece)
        total += len(piece)
    if overflow or total > MAX_RESPONSE_BYTES:
        raise InferenceError("inference response too large")
    return b"".join(parts)


def _content_encoding(response) -> str:
    values = response.headers.get_list("content-encoding", split_commas=True)
    encodings = [value.strip().lower() for value in values if value.strip().lower() != "identity"]
    if not encodings:
        return "identity"
    if len(encodings) != 1 or encodings[0] not in _SUPPORTED_ENCODINGS:
        raise InferenceError("inference response failed")
    return encodings[0]


class _Decoder:
    """Decode identity, gzip, or deflate without holding more than the caller allows."""

    def __init__(self, encoding: str) -> None:
        self.encoding = encoding
        self._raw_deflate = False
        if encoding == "identity":
            self._zlib = None
        elif encoding in {"gzip", "x-gzip"}:
            self._zlib = zlib.decompressobj(zlib.MAX_WBITS | 16)
        elif encoding == "deflate":
            self._zlib = zlib.decompressobj()
            self._raw_deflate = True
        else:
            raise InferenceError("inference response failed")

    def feed(self, data: bytes, room: int) -> tuple[bytes, bool]:
        if room <= 0:
            return b"", bool(data)
        if self._zlib is None:
            if len(data) > room:
                return data[:room], True
            return data, False
        try:
            out = self._zlib.decompress(data, room)
        except zlib.error as exc:
            if self._raw_deflate:
                self._raw_deflate = False
                self._zlib = zlib.decompressobj(-zlib.MAX_WBITS)
                return self.feed(data, room)
            raise InferenceError("inference response failed") from exc
        return out, bool(self._zlib.unconsumed_tail)

    def finish(self, room: int) -> tuple[bytes, bool]:
        if self._zlib is None:
            return b"", False
        try:
            out = self._zlib.flush()
        except zlib.error as exc:
            raise InferenceError("inference response failed") from exc
        if len(out) > room:
            return out[: max(room, 0)], True
        return out, False


def _parse_content(raw: bytes) -> str:
    try:
        payload = json.loads(raw.decode("utf-8"))
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError, UnicodeError) as exc:
        raise InferenceError("inference response was not a chat completion") from exc
    if not isinstance(content, str) or not content.strip():
        raise InferenceError("inference response was empty")
    return content
