"""Apply one unified diff inside a workspace. The diff is text, not a command."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from wrkhorse.sandbox import SandboxError, assert_relative_path, open_for_write

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class DiffError(Exception):
    """The diff is not a single safe file edit."""


@dataclass(frozen=True)
class Hunk:
    old_start: int
    old_len: int
    new_start: int
    new_len: int
    lines: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class FileDiff:
    path: str
    hunks: tuple[Hunk, ...]


def strip_fences(text: str) -> str:
    body = text.strip()
    if body.startswith("```"):
        lines = body.splitlines()
        if len(lines) >= 2 and lines[-1].startswith("```"):
            body = "\n".join(lines[1:-1])
    if not body.endswith("\n"):
        body += "\n"
    return body


def parse_unified(text: str, *, max_bytes: int) -> FileDiff:
    cleaned = strip_fences(text)
    if len(cleaned.encode("utf-8")) > max_bytes:
        raise DiffError("diff too large")
    raw = cleaned.splitlines(keepends=True)
    while raw and (raw[0].startswith("diff --git ") or raw[0].startswith("index ")):
        raw.pop(0)
    if not raw or not raw[0].startswith("--- "):
        raise DiffError("not a unified diff")
    if sum(1 for line in raw if line.startswith("--- ")) != 1:
        raise DiffError("exactly one file is allowed")
    if len(raw) < 3 or not raw[1].startswith("+++ "):
        raise DiffError("not a unified diff")
    old_path = _header_path(raw[0])
    new_path = _header_path(raw[1])
    if old_path != new_path:
        raise DiffError("rename is not allowed")
    hunks: list[Hunk] = []
    index = 2
    while index < len(raw):
        match = _HUNK.match(raw[index])
        if match is None:
            raise DiffError("bad hunk header")
        old_start = int(match.group(1))
        old_len = int(match.group(2) or "1")
        new_start = int(match.group(3))
        new_len = int(match.group(4) or "1")
        index += 1
        body: list[tuple[str, str]] = []
        while index < len(raw) and not raw[index].startswith("@@"):
            line = raw[index]
            if line.startswith("\\"):
                raise DiffError("newline markers are not supported")
            if not line or line[0] not in " +-":
                raise DiffError("bad hunk line")
            content = line[1:]
            if not content.endswith("\n"):
                content += "\n"
            body.append((line[0], content))
            index += 1
        old_count = sum(1 for tag, _ in body if tag in " -")
        new_count = sum(1 for tag, _ in body if tag in " +")
        if old_count != old_len or new_count != new_len:
            raise DiffError("hunk counts do not match")
        if old_start < 1:
            raise DiffError("new files are not allowed")
        hunks.append(
            Hunk(old_start, old_len, new_start, new_len, tuple(body))
        )
        if len(hunks) > 20:
            raise DiffError("too many hunks")
    if not hunks:
        raise DiffError("diff has no hunks")
    return FileDiff(old_path, tuple(hunks))


def _header_path(line: str) -> str:
    rest = line[4:].strip()
    if rest.startswith(("a/", "b/")):
        rest = rest[2:]
    if rest in {"/dev/null", "dev/null"}:
        raise DiffError("new files and deletions are not allowed")
    try:
        assert_relative_path(rest)
    except SandboxError as exc:
        raise DiffError("path not allowed") from exc
    return rest


def apply_hunks(original: str, diff: FileDiff) -> str:
    if "\0" in original:
        raise DiffError("binary file refused")
    lines = original.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    output: list[str] = []
    cursor = 0
    for hunk in diff.hunks:
        start = hunk.old_start - 1
        if start < cursor or start > len(lines):
            raise DiffError("hunk does not apply")
        output.extend(lines[cursor:start])
        cursor = start
        for tag, content in hunk.lines:
            if tag == " ":
                if cursor >= len(lines) or lines[cursor] != content:
                    raise DiffError("context mismatch")
                output.append(lines[cursor])
                cursor += 1
            elif tag == "-":
                if cursor >= len(lines) or lines[cursor] != content:
                    raise DiffError("delete mismatch")
                cursor += 1
            elif tag == "+":
                output.append(content)
            else:
                raise DiffError("bad hunk line")
    output.extend(lines[cursor:])
    return "".join(output)


def apply_diff(root, relative: str, diff: FileDiff, original: str) -> str:
    if diff.path != relative:
        raise DiffError("path is not allowed for this task")
    updated = apply_hunks(original, diff)
    fd = open_for_write(root, relative)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(updated)
    return updated
