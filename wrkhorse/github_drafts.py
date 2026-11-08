"""Pi-style read/write/edit tools, strictly scoped to GitHub draft-post PRs.

The only network destination is the GitHub REST API. The model cannot select
the repository, GitHub API URL, branch, file directory, command, or credential.
A write creates a *draft PR* against main; it NEVER commits to main.
Edits are unique-match replacements on branch files backed by a local draft ID.
No git, shell, delete, merge, or publish capability is exposed.
"""
from __future__ import annotations

import base64
import json
import os
import re
import threading
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import (
    HTTPRedirectHandler, ProxyHandler, Request, build_opener,
)

MAX_FILE_BYTES = 12_000
MAX_RESPONSE_BYTES = 100_000
MAX_DRAFTS = 10
_PATH = re.compile(r"posts/drafts/[a-z0-9][a-z0-9-]{0,49}\.md\Z")
_REPO = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}\Z")
_DRAFT_ID = re.compile(r"[0-9a-f]{32}\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_BRANCH = re.compile(r"wrkhorse-draft-[0-9a-f]{32}\Z")


class DraftError(Exception):
    """Safe public-facing failure: never exposes credentials or GitHub API bodies."""


class GitHubError(DraftError):
    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__("github_not_found" if status == 404 else
                         "github_conflict" if status in (409, 422) else
                         "github_permission_denied" if status in (401, 403) else
                         "github_request_failed")


def validate_repo(repo: str) -> str:
    if not _REPO.fullmatch(repo) or any(
        part in {".", ".."} or part.startswith(".") for part in repo.split("/")
    ):
        raise DraftError("github_repo_must_be_owner_name")
    return repo


def validate_path(path: str) -> str:
    if not isinstance(path, str) or _PATH.fullmatch(path) is None:
        raise DraftError("only_posts_drafts_slug_md_allowed")
    return path


def validate_content(content: str, *, limit: int = MAX_FILE_BYTES) -> str:
    if not isinstance(content, str) or not content.strip() or "\x00" in content:
        raise DraftError("invalid_draft_content")
    if len(content.encode("utf-8")) > limit:
        raise DraftError("draft_content_too_large")
    return content


def validate_title(title: str) -> str:
    if (not isinstance(title, str) or not 1 <= len(title) <= 100 or
            title.strip() != title or any(ord(char) < 32 for char in title)):
        raise DraftError("invalid_draft_title")
    return title


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHubAPI:
    def __init__(self, repo: str, token: str) -> None:
        self.repo = validate_repo(repo)
        if not isinstance(token, str) or len(token) < 16:
            raise DraftError("github_token_required")
        self.token = token
        self.opener = build_opener(ProxyHandler({}), _NoRedirect())

    def call(self, method: str, suffix: str, body: dict | None = None) -> dict:
        """Only fixed internal callers choose method/suffix. Never model input."""
        if method not in {"GET", "POST", "PUT"} or not suffix.startswith("/"):
            raise DraftError("github_operation_refused")
        url = "https://api.github.com/repos/" + self.repo + suffix
        payload = None if body is None else json.dumps(body).encode("utf-8")
        req = Request(
            url,
            data=payload,
            method=method,
            headers={
                "Authorization": "Bearer " + self.token,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "wrkhorse-github-drafts/1",
                **({"Content-Type": "application/json"} if body is not None else {}),
            },
        )
        try:
            with self.opener.open(req, timeout=12) as response:
                if int(response.status) < 200 or int(response.status) >= 300:
                    raise GitHubError(int(response.status))
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            raise GitHubError(exc.code) from None
        except (URLError, OSError, TimeoutError):
            raise DraftError("github_unavailable") from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise DraftError("github_response_too_large")
        try:
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError("not object")
            return result
        except (UnicodeError, ValueError):
            raise DraftError("github_invalid_response") from None


class GitHubDrafts:
    """Persists opaque draft IDs locally. No GitHub credentials in state files."""

    def __init__(
        self,
        *,
        repo: str,
        token: str,
        state_dir: Path,
        api: GitHubAPI | None = None,
    ) -> None:
        self.repo = validate_repo(repo)
        self.api = api if api is not None else GitHubAPI(repo, token)
        self.state_dir = Path(state_dir)
        self.lock = threading.RLock()

    def _file(self, path: str, branch: str) -> dict:
        validate_path(path)
        if branch != "main" and _BRANCH.fullmatch(branch) is None:
            raise DraftError("github_branch_refused")
        suffix = "/contents/" + quote(path, safe="/") + "?ref=" + quote(branch, safe="")
        result = self.api.call("GET", suffix)
        raw = result.get("content")
        sha = result.get("sha")
        if result.get("type") != "file" or not isinstance(raw, str):
            raise DraftError("github_not_a_text_file")
        if not isinstance(sha, str) or _SHA.fullmatch(sha) is None:
            raise DraftError("github_file_sha_invalid")
        try:
            content = base64.b64decode(raw, validate=False).decode("utf-8")
        except (ValueError, UnicodeError):
            raise DraftError("github_invalid_file_content") from None
        validate_content(content)
        return {"path": path, "content": content, "sha": sha, "branch": branch}

    def _state(self, draft_id: str) -> dict:
        if not isinstance(draft_id, str) or _DRAFT_ID.fullmatch(draft_id) is None:
            raise DraftError("unknown_draft")
        try:
            state = json.loads((self.state_dir / (draft_id + ".json")).read_text("utf-8"))
        except (OSError, ValueError, UnicodeError):
            raise DraftError("unknown_draft") from None
        if (not isinstance(state, dict) or state.get("repo") != self.repo or
                state.get("branch") != "wrkhorse-draft-" + draft_id or
                not isinstance(state.get("path"), str)):
            raise DraftError("unknown_draft")
        validate_path(state["path"])
        return state

    def read_file(self, path: str, draft_id: str = "") -> dict:
        """Read one approved Markdown draft on main or a locally tracked PR branch."""
        validate_path(path)
        branch = "main"
        if draft_id:
            state = self._state(draft_id)
            if path != state["path"]:
                raise DraftError("draft_path_mismatch")
            branch = state["branch"]
        return self._file(path, branch)

    def write_file(self, path: str, content: str, title: str) -> dict:
        """Create an entirely new Markdown file on a new branch and open draft PR."""
        validate_path(path)
        validate_content(content)
        validate_title(title)
        with self.lock:
            self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            if len(list(self.state_dir.glob("*.json"))) >= MAX_DRAFTS:
                raise DraftError("draft_limit_reached_review_existing_prs")
            # Prevent accidental overwrite of a file on main.
            try:
                self._file(path, "main")
            except GitHubError as exc:
                if exc.status != 404:
                    raise
            else:
                raise DraftError("draft_file_already_exists_on_main")
            ref = self.api.call("GET", "/git/ref/heads/main")
            sha = ((ref.get("object") or {}).get("sha"))
            if not isinstance(sha, str) or _SHA.fullmatch(sha) is None:
                raise DraftError("github_base_ref_invalid")
            draft_id = uuid.uuid4().hex
            branch = "wrkhorse-draft-" + draft_id
            self.api.call("POST", "/git/refs", {
                "ref": "refs/heads/" + branch,
                "sha": sha,
            })
            self.api.call("PUT", "/contents/" + quote(path, safe="/"), {
                "message": "draft: add " + path,
                "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
                "branch": branch,
            })
            pr = self.api.call("POST", "/pulls", {
                "head": branch,
                "base": "main",
                "title": "Draft post: " + title,
                "body": (
                    "Created by wrkhorse GitHub draft tools.\n\n"
                    "**Human review required.** This PR is not published, merged, "
                    "or applied to main automatically."
                ),
                "draft": True,
            })
            # Verify the provider really created a *draft* PR.
            url = pr.get("html_url")
            if not pr.get("draft") or not isinstance(url, str) or not url.startswith(
                "https://github.com/" + self.repo + "/pull/"
            ):
                raise DraftError("github_draft_pr_not_confirmed")
            state = {"repo": self.repo, "branch": branch, "path": path, "url": url}
            name = self.state_dir / (draft_id + ".json")
            try:
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as out:
                    json.dump(state, out)
            except OSError:
                raise DraftError("draft_record_write_failed") from None
            return {
                "draft_id": draft_id, "path": path, "branch": branch,
                "draft_pr_url": url, "status": "draft_pr",
                "requires_human_review": True, "merged": False,
            }

    def edit_file(self, draft_id: str, old_text: str, new_text: str) -> dict:
        """Pi-style exact, unique match edit with GitHub's SHA conflict guard."""
        if not isinstance(old_text, str) or not old_text or len(old_text.encode("utf-8")) > 4_000:
            raise DraftError("invalid_old_text")
        if not isinstance(new_text, str) or len(new_text.encode("utf-8")) > 8_000 or "\x00" in new_text:
            raise DraftError("invalid_new_text")
        with self.lock:
            state = self._state(draft_id)
            current = self._file(state["path"], state["branch"])
            if current["content"].count(old_text) != 1:
                raise DraftError("old_text_must_match_exactly_once")
            updated = current["content"].replace(old_text, new_text, 1)
            validate_content(updated)
            commit = self.api.call("PUT", "/contents/" + quote(state["path"], safe="/"), {
                "message": "draft: edit " + state["path"],
                "content": base64.b64encode(updated.encode("utf-8")).decode("ascii"),
                "sha": current["sha"],
                "branch": state["branch"],
            })
            if not isinstance(commit.get("commit"), dict):
                raise DraftError("github_edit_not_confirmed")
            return {
                "draft_id": draft_id, "path": state["path"], "draft_pr_url": state["url"],
                "status": "draft_updated", "requires_human_review": True, "merged": False,
            }
