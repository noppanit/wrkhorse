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
import re
from collections.abc import Callable
from urllib.parse import urlsplit, urlunsplit

import httpx2

from wrkhorse.local_model import (
    DEFAULT_BASE_URL,
    DUMMY_API_KEY,
    LocalModelError,
    validate_base_url,
    validate_model_tag,
)

MAX_RESPONSE_BYTES = 100_000
_REDIRECTS = {301, 302, 303, 307, 308}
_REQUEST_HEADERS = {
    "Accept": "application/json",
    "Accept-Encoding": "identity",
}


# Reasoning models served through OpenAI-compatible APIs (qwen3, deepseek-r1,
# some llama.cpp/LM Studio builds) may put their chain of thought inline.
_THINK = re.compile(r"^\s*<(think|thinking|reasoning)>.*?</\1>\s*", re.DOTALL | re.IGNORECASE)


class InferenceError(Exception):
    """The model call did not produce a usable diff."""


class InferenceNotConfigured(InferenceError):
    """No model URL, model tag or credential; no request was sent."""


class InferenceTimeout(InferenceError):
    """The single overall deadline expired."""


class InferenceEmptyResponse(InferenceError):
    """The model answered, but with no usable text (empty or truncated)."""


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
    max_tokens: int = 1024,
) -> str:
    if not api_key or not model:
        raise InferenceNotConfigured("inference is not configured")
    if timeout <= 0:
        raise InferenceTimeout("inference deadline exceeded")
    endpoint = chat_endpoint(base_url)
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": max_tokens,
        "stream": False,
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
        raise InferenceTimeout("inference deadline exceeded") from exc


def request_local_chat(
    *,
    model: str,
    prompt: str,
    timeout: float = 90,
    base_url: str = DEFAULT_BASE_URL,
    allow_lan: bool = False,
    max_tokens: int = 1024,
) -> str:
    """Text-only local-model tool. No shell, files, or cloud inference endpoints.

    This is an opt-in separate gateway tool; the normal queued coding worker
    remains isolated. The URL must pass the local-model policy (loopback, or a
    private LAN host when explicitly allowed), the credential is a dummy, and
    prompts and replies are bounded.
    """
    try:
        validate_model_tag(model)
        # Config already resolved and checked the host; re-check syntax here
        # so a caller cannot pass an arbitrary URL.
        local_base, _scope = validate_base_url(base_url, allow_lan=allow_lan, resolve=False)
    except LocalModelError as exc:
        raise InferenceNotConfigured("local model is not configured") from exc
    if not isinstance(prompt, str) or not prompt.strip():
        raise InferenceError("local model prompt is empty")
    if len(prompt.encode("utf-8")) > 4096:
        raise InferenceError("local model prompt too large")
    if not 0 < timeout <= 300:
        raise InferenceError("invalid local model deadline")
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": max_tokens,
        "stream": False,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a local assistant. Answer the question concisely. "
                    "Do not claim to have executed code, edited files, accessed "
                    "external services, or performed actions."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    }
    try:
        answer = asyncio.run(_bounded(
            chat_endpoint(local_base),
            DUMMY_API_KEY,
            body,
            timeout,
            None,
        ))
    except TimeoutError as exc:
        raise InferenceTimeout("local model deadline exceeded") from exc
    if len(answer.encode("utf-8")) > 16_384:
        raise InferenceError("local model output too large")
    return answer


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


def strip_reasoning(content: str) -> str:
    """Drop a leading <think>...</think> block emitted by reasoning models."""
    stripped = _THINK.sub("", content, count=1)
    if re.match(r"^\s*<(think|thinking|reasoning)>", stripped, re.IGNORECASE):
        # Opened but never closed: the model ran out of tokens while thinking.
        return ""
    return stripped


def _parse_content(raw: bytes) -> str:
    try:
        payload = json.loads(raw.decode("utf-8"))
        choice = payload["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError, UnicodeError) as exc:
        raise InferenceError("inference response was not a chat completion") from exc
    if content is None:
        content = ""
    if not isinstance(content, str):
        raise InferenceError("inference response was not a chat completion")
    content = strip_reasoning(content)
    if not content.strip():
        if isinstance(choice, dict) and choice.get("finish_reason") == "length":
            raise InferenceEmptyResponse(
                "inference output was truncated; raise WRKHORSE_INFERENCE_MAX_TOKENS "
                "or use a non-reasoning model"
            )
        raise InferenceEmptyResponse("inference response was empty")
    return content
