"""Read-only file listing and reading under one configured root.

The root comes from ``WRKHORSE_FILES_ROOT``. When that variable is unset the
tools report that they are disabled; they never fall back to ``/``. Paths are
relative to the root. Absolute paths and ``..`` are rejected, and every
symlink is resolved so a link cannot point outside the root. Secret-looking
paths are blocked even when the root is wide.

Nothing here writes, deletes, renames, changes modes, or executes file contents.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

MAX_READ_BYTES = 256 * 1024
HARD_MAX_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_ENTRIES = 200
MAX_ENTRIES = 1000
DEFAULT_LINE_LIMIT = 400
MAX_LINE_LIMIT = 5000
MAX_LINE_OFFSET = 1_000_000

DISABLED_MESSAGE = (
    "local file tools are disabled; set WRKHORSE_FILES_ROOT to an absolute directory"
)
DENYLIST_SUMMARY = (
    ".env and .env.*, *.pem, *.key, *.p12, *.pfx, *.ppk, "
    "SSH keys (id_rsa, id_ed25519, id_ecdsa, id_dsa, id_xmss), "
    ".ssh/, .aws/, .gnupg/, .git/, .netrc, credential and keychain files"
)

_DENIED_DIRECTORIES = {".ssh", ".aws", ".gnupg", ".git"}
_CREDENTIAL_NAMES = {
    ".netrc",
    "_netrc",
    "credentials",
    "credentials.json",
    "credentials.yml",
    "credentials.yaml",
    "credentials.xml",
    "credential",
    "credential.json",
}
_KEY_SUFFIXES = (".pem", ".key", ".keychain", ".keychain-db", ".p12", ".pfx", ".ppk")
_SSH_PREFIXES = ("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "id_xmss")


class FilesError(Exception):
    """Safe message for a refused read. It never includes file contents."""


class FilesConfigError(Exception):
    """WRKHORSE_FILES_ROOT is set to something the gateway must not use."""


def parse_files_root(raw: str | None) -> Path | None:
    """Return the resolved root, or None when the setting is unset.

    An empty value is disabled, same as omitting the variable. A relative
    path, a missing path, or a non-directory is a configuration error so a
    typo cannot silently widen or disable access.
    """
    if raw is None or raw.strip() == "":
        return None
    if raw != raw.strip():
        raise FilesConfigError(
            "WRKHORSE_FILES_ROOT must not begin or end with whitespace"
        )
    path = Path(raw)
    if not path.is_absolute():
        raise FilesConfigError("WRKHORSE_FILES_ROOT must be an absolute path")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise FilesConfigError("WRKHORSE_FILES_ROOT does not exist") from exc
    if not resolved.is_dir():
        raise FilesConfigError("WRKHORSE_FILES_ROOT must be a directory")
    if _path_is_blocked(resolved):
        raise FilesConfigError(
            "WRKHORSE_FILES_ROOT is a blocked secrets directory and cannot be used"
        )
    return resolved


def _component_blocked(name: str) -> bool:
    lowered = name.lower()
    if lowered in _DENIED_DIRECTORIES:
        return True
    if lowered == ".env" or lowered.startswith(".env."):
        return True
    if lowered in _CREDENTIAL_NAMES:
        return True
    if lowered.startswith(("credentials.", "credential.", ".netrc")):
        return True
    if lowered.endswith(_KEY_SUFFIXES):
        return True
    for prefix in _SSH_PREFIXES:
        if lowered == prefix or (
            lowered.startswith(prefix) and lowered[len(prefix)] in "._-"
        ):
            return True
    return False


def _path_is_blocked(path: Path) -> bool:
    return any(_component_blocked(part) for part in path.parts)


def _is_inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _lexical(path: object) -> str:
    if not isinstance(path, str) or "\x00" in path or "\\" in path:
        raise FilesError("path is outside the files root")
    if path.startswith(("~", "/")) or Path(path).is_absolute():
        raise FilesError("path is outside the files root")
    parts = [part for part in Path(path).parts if part != "."]
    if any(part in {"", ".."} for part in parts):
        raise FilesError("path is outside the files root")
    if not parts:
        return "."
    return "/".join(parts)


def _bounded_int(value: object, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise FilesError(f"{name} must be an integer between {low} and {high}")
    if value < low or value > high:
        raise FilesError(f"{name} must be an integer between {low} and {high}")
    return value


class LocalFiles:
    """Read files under one resolved directory. ``root`` None means disabled."""

    def __init__(self, root: Path | None) -> None:
        self.root = root

    def list_files(
        self,
        path: str = ".",
        recursive: bool = False,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ) -> dict:
        self._ensure_enabled()
        if not isinstance(recursive, bool):
            raise FilesError("recursive must be true or false")
        limit = _bounded_int(max_entries, "max_entries", 1, MAX_ENTRIES)
        lexical = _lexical(path)
        resolved = self._resolve(lexical)
        if not resolved.is_dir():
            raise FilesError("not a directory")
        info = resolved.stat()
        state = {
            "truncated": False,
            "stop": False,
            "seen": {(info.st_dev, info.st_ino)},
        }
        entries: list[dict] = []
        self._list_dir(
            resolved,
            "" if lexical == "." else lexical,
            recursive,
            limit,
            entries,
            state,
            required=True,
        )
        return {
            "path": lexical,
            "entries": entries,
            "count": len(entries),
            "truncated": state["truncated"],
            "executes_code": False,
        }

    def read_file(
        self,
        path: str,
        offset: int = 1,
        limit: int = DEFAULT_LINE_LIMIT,
    ) -> dict:
        self._ensure_enabled()
        start = _bounded_int(offset, "offset", 1, MAX_LINE_OFFSET)
        line_limit = _bounded_int(limit, "limit", 1, MAX_LINE_LIMIT)
        lexical = _lexical(path)
        if lexical == ".":
            raise FilesError("not a file")
        resolved = self._resolve(lexical)
        if not resolved.is_file():
            raise FilesError("not a file")
        data = self._read_bytes(resolved)
        if b"\x00" in data:
            raise FilesError("binary file refused")
        try:
            text = data.decode("utf-8")
        except UnicodeError:
            raise FilesError("binary file refused") from None
        lines = text.splitlines(keepends=True)
        selected = lines[start - 1 : start - 1 + line_limit]
        chosen: list[str] = []
        size = 0
        hit_cap = False
        for line in selected:
            encoded = len(line.encode("utf-8"))
            if size + encoded > MAX_READ_BYTES:
                hit_cap = True
                break
            chosen.append(line)
            size += encoded
        if hit_cap and not chosen:
            raise FilesError("file is too large")
        end = (start - 1) + len(chosen)
        return {
            "path": lexical,
            "content": "".join(chosen),
            "offset": start,
            "limit": line_limit,
            "lines_returned": len(chosen),
            "total_lines": len(lines),
            "truncated": hit_cap or end < len(lines),
            "executes_code": False,
        }

    def _ensure_enabled(self) -> None:
        if self.root is None:
            raise FilesError(DISABLED_MESSAGE)

    def _resolve(self, lexical: str) -> Path:
        assert self.root is not None
        parts = [] if lexical == "." else lexical.split("/")
        if any(_component_blocked(part) for part in parts):
            raise FilesError("path is blocked")
        candidate = self.root.joinpath(*parts)
        try:
            # exists() follows links. A dangling link is still a symlink, and
            # resolve(strict=True) reports it as missing.
            if not candidate.exists() and not candidate.is_symlink():
                raise FilesError("not found")
            resolved = candidate.resolve(strict=True)
        except FilesError:
            raise
        except OSError:
            raise FilesError("not found") from None
        if not _is_inside(self.root, resolved):
            raise FilesError("path is outside the files root")
        if _path_is_blocked(resolved):
            raise FilesError("path is blocked")
        return resolved

    def _read_bytes(self, resolved: Path) -> bytes:
        assert self.root is not None
        flags = os.O_RDONLY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            # The path was already resolved. Refusing a final symlink here
            # closes the gap where the last component is replaced before open.
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(resolved, flags)
        except OSError:
            raise FilesError("not found") from None
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise FilesError("not a file")
            if info.st_size > HARD_MAX_BYTES:
                raise FilesError("file is too large")
            self._assert_fd_inside(fd)
            chunks: list[bytes] = []
            remaining = HARD_MAX_BYTES + 1
            while remaining > 0:
                block = os.read(fd, min(remaining, 65536))
                if not block:
                    break
                chunks.append(block)
                remaining -= len(block)
            data = b"".join(chunks)
            if len(data) > HARD_MAX_BYTES:
                raise FilesError("file is too large")
            return data
        finally:
            os.close(fd)

    def _assert_fd_inside(self, fd: int) -> None:
        assert self.root is not None
        try:
            raw = os.readlink(f"/proc/self/fd/{fd}")
        except OSError:
            return
        if raw.endswith(" (deleted)"):
            raise FilesError("not found")
        try:
            real = Path(raw).resolve(strict=True)
        except OSError:
            raise FilesError("not found") from None
        if not _is_inside(self.root, real):
            raise FilesError("path is outside the files root")
        if _path_is_blocked(real):
            raise FilesError("path is blocked")

    def _list_dir(
        self,
        directory: Path,
        prefix: str,
        recursive: bool,
        limit: int,
        entries: list[dict],
        state: dict,
        required: bool = False,
    ) -> None:
        assert self.root is not None
        try:
            children = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError:
            if required:
                raise FilesError("not found") from None
            return
        for child in children:
            if state["stop"]:
                return
            name = child.name
            try:
                name.encode("utf-8")
            except UnicodeEncodeError:
                continue
            if _component_blocked(name):
                continue
            shown = name if not prefix else f"{prefix}/{name}"
            try:
                resolved = Path(child.path).resolve(strict=True)
            except OSError:
                continue
            if not _is_inside(self.root, resolved) or _path_is_blocked(resolved):
                continue
            try:
                info = resolved.stat()
            except OSError:
                continue
            if stat.S_ISDIR(info.st_mode):
                self._append(entries, state, limit, shown, "dir", 0)
                ident = (info.st_dev, info.st_ino)
                if recursive and not state["stop"] and ident not in state["seen"]:
                    state["seen"].add(ident)
                    self._list_dir(resolved, shown, True, limit, entries, state)
            elif stat.S_ISREG(info.st_mode):
                self._append(entries, state, limit, shown, "file", info.st_size)

    def _append(
        self,
        entries: list[dict],
        state: dict,
        limit: int,
        name: str,
        kind: str,
        size: int,
    ) -> None:
        if len(entries) >= limit:
            state["truncated"] = True
            state["stop"] = True
            return
        entries.append({"name": name, "type": kind, "size": size})
