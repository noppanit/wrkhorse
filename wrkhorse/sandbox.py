"""Workspace copy and path checks.

A temporary directory plus an allowlisted test command is not a sandbox.
Remote execution stays disabled unless the worker process was started with
WRKHORSE_EXECUTION_MODE=trusted-local-demo, and even that mode does not run
model-written code.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


class SandboxError(Exception):
    """A path is not safe to use as a workspace file."""


def copy_tree(source: Path, dest: Path) -> None:
    """Copy a fixture tree. Symlinks and special files are refused."""
    if source.is_symlink() or not source.is_dir():
        raise SandboxError("fixture root must be a real directory")
    dest.mkdir(parents=True, exist_ok=False)
    for entry in source.iterdir():
        target = dest / entry.name
        if entry.is_symlink():
            raise SandboxError("fixture contains a symlink")
        if entry.is_dir():
            copy_tree(entry, target)
        elif entry.is_file():
            shutil.copyfile(entry, target)
        else:
            raise SandboxError("fixture contains an unsupported file")


def _root(path: Path) -> Path:
    resolved = path.resolve()
    if path.is_symlink():
        raise SandboxError("symlink refused")
    return resolved


def assert_relative_path(rel: str) -> Path:
    candidate = Path(rel)
    if candidate.is_absolute() or "\\" in rel:
        raise SandboxError("path not allowed")
    if any(part in {"", ".", ".."} for part in candidate.parts):
        raise SandboxError("path not allowed")
    return candidate


def open_for_write(root: Path, rel: str) -> int:
    """Open a file inside root. Existing symlinks and escapes fail."""
    relative = assert_relative_path(rel)
    root_real = _root(root)
    current = root_real
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink() or not current.is_dir():
            raise SandboxError("path not allowed")
        if root_real != current and root_real not in current.parents:
            raise SandboxError("path escapes workspace")
    target = current / relative.parts[-1]
    if target.is_symlink():
        raise SandboxError("symlink refused")
    if target.exists() and not target.is_file():
        raise SandboxError("path not allowed")
    parent = target.parent.resolve()
    if parent != root_real and root_real not in parent.parents:
        raise SandboxError("path escapes workspace")
    flags = os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW
    if not target.exists():
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    try:
        return os.open(target, flags, 0o644)
    except OSError as exc:
        raise SandboxError("path not allowed") from exc
