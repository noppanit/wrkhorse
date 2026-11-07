"""Build/validate a bounded Runpod Serverless coding request for ChatGPT.

This module does not call Runpod, start a GPU, execute model-written code,
apply a model-written diff to this repository, or create a PR. ChatGPT can
submit build_sample_job() through its connected Runpod endpoint tool.
"""

from __future__ import annotations

from typing import Any

from wrkhorse.diffutil import DiffError, apply_hunks, parse_unified, strip_fences
from wrkhorse.fixtures import load_sample, prompt_for

MODEL_ALIAS = "wrkhorse-coder"
ALLOWED_PATH = "adder/mathy.py"
MAX_REPLY_BYTES = 32_768
MAX_PROMPT_BYTES = 16_384


class RunpodPilotError(Exception):
    """Worker response was not a valid, bounded sample coding result."""


def build_sample_job() -> dict[str, Any]:
    """Return the input object for Runpod run_endpoint / runsync_endpoint.

    This request uses the published llama.cpp Serverless worker's documented
    openai_route/openai_input format, with a fixed model alias.
    """
    manifest, _reviewed_patch, fixture_root = load_sample()
    prompt = prompt_for(manifest, fixture_root)
    if manifest.get("allowed_paths") != [ALLOWED_PATH]:
        raise RunpodPilotError("sample fixture allowlist has changed")
    if len(prompt.encode("utf-8")) > MAX_PROMPT_BYTES:
        raise RunpodPilotError("prompt too large")
    return {
        "openai_route": "/v1/chat/completions",
        "openai_input": {
            "model": MODEL_ALIAS,
            "temperature": 0,
            "max_tokens": 1024,
            "stream": False,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a coding assistant. Reply with exactly one "
                        "unified diff for the requested file, no markdown, "
                        "no commentary, no shell commands, no extra files. "
                        "Never propose to run commands or deploy software."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        },
    }


def validate_sample_result(result: Any) -> dict[str, str]:
    """Validate the output of a COMPLETED Runpod Serverless job.

    The Runpod adapter may aggregate one non-streaming OpenAI result into a
    one-element output list. Parsing the patch is data-only; no code is run.
    """
    output = _unwrap_completed(result)
    if not isinstance(output, dict) or output.get("error"):
        raise RunpodPilotError("worker returned an error")
    try:
        content = output["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RunpodPilotError("worker did not return a chat completion") from exc
    if not isinstance(content, str) or not content.strip():
        raise RunpodPilotError("worker returned empty content")
    if len(content.encode("utf-8")) > MAX_REPLY_BYTES:
        raise RunpodPilotError("model response too large")
    try:
        file_diff = parse_unified(content, max_bytes=MAX_REPLY_BYTES)
    except DiffError as exc:
        raise RunpodPilotError("model returned an invalid diff") from exc
    if file_diff.path != ALLOWED_PATH:
        raise RunpodPilotError("model changed a disallowed path")
    manifest, _reviewed_patch, repo = load_sample()
    if manifest.get("allowed_paths") != [ALLOWED_PATH]:
        raise RunpodPilotError("sample fixture allowlist has changed")
    original = (repo / ALLOWED_PATH).read_text(encoding="utf-8")
    try:
        modified = apply_hunks(original, file_diff)
    except DiffError as exc:
        raise RunpodPilotError("model diff does not apply") from exc
    if modified == original:
        raise RunpodPilotError("model diff made no changes")
    return {
        "status": "diff_ready",
        "model": MODEL_ALIAS,
        "path": ALLOWED_PATH,
        "diff": strip_fences(content),
        "proposed_content": modified,
    }


def _unwrap_completed(result: Any) -> Any:
    if not isinstance(result, dict):
        raise RunpodPilotError("unexpected Runpod response")
    if "status" in result:
        if result["status"] != "COMPLETED":
            raise RunpodPilotError("job is not completed")
        result = result.get("output")
    else:
        result = result.get("output", result)
    if isinstance(result, list):
        if len(result) != 1:
            raise RunpodPilotError("worker must return one chat completion")
        result = result[0]
    return result
