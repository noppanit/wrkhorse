"""Run the fixed unit-test command inside a workspace directory.

The command is a constant in this file. Callers cannot supply a shell string.
The child environment is built from scratch and does not inherit the parent
process environment. This is still an ordinary subprocess, not a sandbox.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_LOADER = """
import pathlib
import sys
import unittest

sys.dont_write_bytecode = True
root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
suite = unittest.defaultTestLoader.discover(
    str(root / "tests"), pattern="test_*.py", top_level_dir=str(root)
)
result = unittest.TextTestRunner(verbosity=2, stream=sys.stdout).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
"""


@dataclass(frozen=True)
class TestRun:
    returncode: int
    output: str
    timed_out: bool

    @property
    def passed(self) -> bool:
        return self.returncode == 0 and not self.timed_out


def _child_env(workspace: Path) -> dict[str, str]:
    tmp = workspace / ".tmp"
    tmp.mkdir(exist_ok=True)
    return {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "HOME": str(workspace),
        "TMPDIR": str(tmp),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONUNBUFFERED": "1",
    }


def _truncate(text: str, limit: int = 8000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[truncated]\n"


def _clear_bytecode(workspace: Path) -> None:
    for cache in workspace.rglob("__pycache__"):
        if cache.is_symlink():
            raise RuntimeError("refusing to follow a bytecode symlink")
        for child in cache.iterdir():
            if child.is_file() and not child.is_symlink():
                child.unlink()
        cache.rmdir()


def run_unittest(workspace: Path, *, timeout: float) -> TestRun:
    """Run the reviewed fixture tests. Do not call this on model-written code.

    ``python -I`` ignores ``PYTHON*`` environment variables, so bytecode is
    disabled with ``-B`` and any cache already on disk is removed first.
    """
    if not workspace.is_dir() or workspace.is_symlink():
        raise RuntimeError("workspace is not a real directory")
    _clear_bytecode(workspace)
    proc = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", _LOADER, str(workspace.resolve())],
        cwd=workspace,
        env=_child_env(workspace),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        start_new_session=True,
    )
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(proc.pid, signal.SIGKILL)
        stdout, stderr = proc.communicate()
    combined = (stdout or "") + (stderr or "")
    root = str(workspace.resolve())
    combined = combined.replace(root, "[workspace]")
    return TestRun(proc.returncode if proc.returncode is not None else 1, _truncate(combined), timed_out)
