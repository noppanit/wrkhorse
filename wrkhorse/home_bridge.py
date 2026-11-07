"""Outbound-only ChatGPT/GitHub → MacBook/Ollama fixture pilot.

The only accepted remote task is the repository's public sample-add fixture.
No shell command, repository path, URL, or code from a GitHub issue is executed.
Requires local gh CLI authentication and a local Ollama app.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import URLError

from wrkhorse.fixtures import load_sample, prompt_for
from wrkhorse.runpod_pilot import RunpodPilotError, validate_sample_result

REPO = "noppanit/wrkhorse"
OWNER = "noppanit"
LABEL = "wrkhorse-home"
TASK_BODY = "WRKHORSE_HOME_V1\ntask: sample-add"
RESULT_MARKER = "<!-- wrkhorse-home-result-v1 -->"
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
DEFAULT_MODEL = "qwen2.5-coder:1.5b"


class HomeBridgeError(Exception):
    """An expected bridge or inference failure."""


def eligible_issue(issue: dict) -> bool:
    """Fail closed on any issue except the owner-authored, labeled test fixture."""
    labels = {item.get("name") for item in issue.get("labels", []) if isinstance(item, dict)}
    return (
        issue.get("author", {}).get("login", "").lower() == OWNER
        and issue.get("body", "").strip() == TASK_BODY
        and LABEL in labels
        and issue.get("state", "OPEN").upper() == "OPEN"
    )


def has_result(issue: dict) -> bool:
    """Avoid repeating a request already answered by this local pilot."""
    return any(
        RESULT_MARKER in str(comment.get("body", ""))
        and comment.get("author", {}).get("login", "").lower() == OWNER
        for comment in issue.get("comments", [])
        if isinstance(comment, dict)
    )


def gh_json(*args: str):
    try:
        run = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HomeBridgeError("GitHub CLI failed; check that gh is installed and authenticated") from exc
    if run.returncode:
        raise HomeBridgeError("GitHub CLI request failed; check gh auth status and repository access")
    try:
        return json.loads(run.stdout)
    except json.JSONDecodeError as exc:
        raise HomeBridgeError("GitHub CLI returned invalid JSON") from exc


def find_pending() -> dict | None:
    issues = gh_json(
        "issue", "list", "-R", REPO,
        "--state", "open", "--label", LABEL,
        "--limit", "20",
        "--json", "number,body,author,labels,state",
    )
    if not isinstance(issues, list):
        raise HomeBridgeError("GitHub issue list was not a list")
    for candidate in issues:
        if not isinstance(candidate, dict) or not eligible_issue(candidate):
            continue
        number = candidate.get("number")
        if not isinstance(number, int) or number <= 0:
            continue
        issue = gh_json(
            "issue", "view", str(number), "-R", REPO,
            "--json", "number,body,author,labels,comments,state",
        )
        if eligible_issue(issue) and not has_result(issue):
            return issue
    return None


def read_issue(number: int) -> dict:
    issue = gh_json(
        "issue", "view", str(number), "-R", REPO,
        "--json", "number,body,author,labels,comments,state",
    )
    if not eligible_issue(issue):
        raise HomeBridgeError("Issue does not match the owner, label and exact safe task")
    if has_result(issue):
        raise HomeBridgeError("Issue already has a home-bridge result")
    return issue


def request_local_model(model: str) -> str:
    manifest, _patch, repo = load_sample()
    if manifest.get("allowed_paths") != ["adder/mathy.py"]:
        raise HomeBridgeError("Unexpected fixture configuration")
    prompt = prompt_for(manifest, repo)
    prompt += (
        "\nThe original file has exactly TWO lines. "
        "Use the hunk header @@ -1,2 +1,2 @@ with one context line, "
        "one removed line and one added line. No markdown fences."
    )
    data = json.dumps({
        "model": model,
        "stream": False,
        "keep_alive": "1m",
        "messages": [
            {
                "role": "system",
                "content": (
                    "Produce only one unified diff for the allowed file, "
                    "no commentary, code fences, or shell commands. "
                    "Do not execute any code."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "options": {"temperature": 0, "num_predict": 512, "num_ctx": 2048},
    }).encode("utf-8")
    request = Request(
        OLLAMA_URL, data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    # Explicitly ignore HTTP(S)_PROXY: model requests must stay on loopback.
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(request, timeout=180) as response:
            raw = response.read(100_001)
    except (OSError, URLError, TimeoutError) as exc:
        raise HomeBridgeError("Cannot reach local Ollama; open Ollama and pull the selected model") from exc
    if len(raw) > 100_000:
        raise HomeBridgeError("Local model response is too large")
    try:
        data = json.loads(raw)
        answer = data["message"]["content"]
    except (ValueError, TypeError, KeyError) as exc:
        raise HomeBridgeError("Ollama did not return a chat message") from exc
    if not isinstance(answer, str) or not answer.strip():
        raise HomeBridgeError("Local model returned an empty message")
    return answer


def validate_ollama_answer(answer: str) -> dict[str, str]:
    return validate_sample_result({
        "status": "COMPLETED",
        "output": [{"choices": [{"message": {"content": answer}}]}],
    })


def format_comment(outcome: dict[str, str] | None, *, model: str, error: str = "") -> str:
    if outcome is None:
        return (
            f"{RESULT_MARKER}\n"
            f"**Home model result: invalid_diff** (`{model}`).\n\n"
            "The MacBook contacted its local Ollama model but did not produce "
            "a valid one-file patch. No code was executed, applied or merged.\n"
        )
    return (
        f"{RESULT_MARKER}\n"
        f"**Home model result: diff_ready** (`{model}`).\n\n"
        "```diff\n" + outcome["diff"].rstrip() + "\n```\n\n"
        "Validated against the public sample-add fixture. "
        "Model-written code was **not executed, applied or merged**.\n"
    )


def post_comment(number: int, body: str) -> None:
    # No shell or eval; GitHub CLI uses its existing local credential store.
    try:
        run = subprocess.run(
            ["gh", "issue", "comment", str(number), "-R", REPO, "--body", body],
            text=True, capture_output=True, timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HomeBridgeError("Could not submit result through GitHub CLI") from exc
    if run.returncode:
        raise HomeBridgeError("GitHub comment failed; verify write permission and gh auth status")


def process_issue(issue: dict, *, model: str) -> str:
    number = issue["number"]
    try:
        answer = request_local_model(model)
        try:
            outcome = validate_ollama_answer(answer)
        except RunpodPilotError:
            outcome = None
        comment = format_comment(outcome, model=model)
    except HomeBridgeError as exc:
        comment = (
            f"{RESULT_MARKER}\n"
            "**Home model result: model_unavailable**. No code was run. "
            "Check local Ollama setup and retry using a new issue.\n"
        )
        print(f"Local inference failed for #{number}: {exc}", file=sys.stderr)
    post_comment(number, comment)
    return f"Posted local-model result to https://github.com/{REPO}/issues/{number}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Safe outbound GitHub issue → local Ollama fixture bridge")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Run local model only; no GitHub access")
    mode.add_argument("--once", action="store_true", help="Process at most one pending GitHub issue")
    mode.add_argument("--watch", action="store_true", help="Poll GitHub until Ctrl-C")
    mode.add_argument("--issue", type=int, help="Process one explicit issue number")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Local Ollama model tag")
    parser.add_argument("--interval", type=int, default=30, help="Polling seconds, minimum 10")
    args = parser.parse_args(argv)
    if args.interval < 10:
        parser.error("--interval must be >= 10")
    if not args.model or len(args.model) > 100 or ":cloud" in args.model:
        parser.error("select a locally downloaded Ollama model, not a cloud model")
    try:
        if args.check:
            answer = request_local_model(args.model)
            try:
                result = validate_ollama_answer(answer)
                print("Local Ollama model returned a valid diff:\n" + result["diff"])
            except RunpodPilotError:
                print("Local Ollama responded, but its diff was invalid:\n" + answer[:1500])
            return 0
        if args.issue is not None:
            if args.issue < 1:
                parser.error("--issue must be positive")
            print(process_issue(read_issue(args.issue), model=args.model))
            return 0
        while True:
            issue = find_pending()
            if issue:
                print(process_issue(issue, model=args.model), flush=True)
            elif args.once:
                print("No pending, owner-authored wrkhorse-home sample-add issue.")
            if not args.watch:
                break
            time.sleep(args.interval)
        return 0
    except HomeBridgeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
