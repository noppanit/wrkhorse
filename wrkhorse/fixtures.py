"""The only coding task registered in this pilot."""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = REPO_ROOT / "fixtures" / "sample_task"
TASK_NAME = "sample-add"


def load_sample() -> tuple[dict, str, Path]:
    manifest = json.loads((FIXTURE_ROOT / "task.json").read_text(encoding="utf-8"))
    patch = (FIXTURE_ROOT / "mock.patch").read_text(encoding="utf-8")
    repo = FIXTURE_ROOT / "repo"
    if manifest.get("name") != TASK_NAME:
        raise RuntimeError("sample fixture name mismatch")
    allowed = manifest.get("allowed_paths")
    if allowed != ["adder/mathy.py"]:
        raise RuntimeError("sample fixture allowlist mismatch")
    return manifest, patch, repo


def prompt_for(manifest: dict, repo: Path) -> str:
    parts = [
        manifest["summary"],
        "Allowed files:",
        *[f"- {path}" for path in manifest["allowed_paths"]],
        "Current files:",
    ]
    for relative in manifest["allowed_paths"]:
        text = (repo / relative).read_text(encoding="utf-8")
        parts.append(f"----- {relative}")
        parts.append(text)
    parts.append("Return a unified diff that changes only the allowed files.")
    return "\n".join(parts)
