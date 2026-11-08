"""Network-free tests for Pi-inspired, GitHub-scoped draft-post MCP tools."""
from __future__ import annotations

import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wrkhorse.config import ConfigError, load_gateway_settings
from wrkhorse.github_drafts import (
    DraftError, GitHubAPI, GitHubDrafts, GitHubError, MAX_FILE_BYTES,
    validate_content, validate_path, validate_repo,
)
from wrkhorse.logsetup import ScrubFilter

REPO = "noppanit/wrkhorse"
TOKEN = "github_pat_" + "g" * 35
MCP_TOKEN = "a" * 48
SHA = "a" * 40
CONTENT_SHA = "b" * 40
PATH = "posts/drafts/hello-world.md"


class FakeGitHub:
    def __init__(self):
        self.calls = []
        self.data = {}
        self.branches = {}
        self.closed = False
        self.ready = False
        self.edit_commits = 0

    def call(self, method, suffix, body=None):
        self.calls.append((method, suffix, body))
        if method == "GET" and suffix.startswith("/contents/"):
            stem = suffix[len("/contents/"):]
            path, query = stem.split("?ref=", 1)
            if (query, path) not in self.data:
                raise GitHubError(404)
            text = self.data[(query, path)]
            return {
                "type": "file",
                "sha": CONTENT_SHA,
                "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
            }
        if (method, suffix) == ("GET", "/git/ref/heads/main"):
            return {"object": {"sha": SHA}}
        if (method, suffix) == ("POST", "/git/refs"):
            branch = body["ref"].removeprefix("refs/heads/")
            self.branches[branch] = body["sha"]
            return {"ref": body["ref"]}
        if method == "PUT" and suffix.startswith("/contents/"):
            assert body["branch"].startswith("wrkhorse-draft-")
            assert suffix.startswith("/contents/posts/drafts/")
            self.data[(body["branch"], suffix.removeprefix("/contents/"))] = (
                base64.b64decode(body["content"]).decode("utf-8")
            )
            self.edit_commits += 1
            return {"commit": {"sha": SHA}}
        if (method, suffix) == ("POST", "/pulls"):
            assert body["draft"] is True
            assert body["base"] == "main"
            return {
                "draft": True,
                "number": 101,
                "html_url": "https://github.com/noppanit/wrkhorse/pull/101",
            }
        if (method, suffix) == ("GET", "/pulls/101"):
            branch = next(iter(self.branches))
            return {
                "state": "closed" if self.closed else "open",
                "draft": not self.ready,
                "head": {"ref": branch},
                "base": {"ref": "main"},
            }
        raise AssertionError("unexpected GitHub operation: " + method + " " + suffix)


class DraftToolTests(unittest.TestCase):
    def service(self, directory):
        fake = FakeGitHub()
        draft = GitHubDrafts(repo=REPO, token=TOKEN, state_dir=directory, api=fake)
        return draft, fake

    def test_path_allowlist_and_content_limits(self):
        for path in (
            "../../etc/passwd", "README.md", ".env", "src/code.py",
            "posts/drafts/../secrets.md", "posts/drafts/.hidden.md",
            "posts/drafts/OK.md", "posts/drafts/x.md/other.md",
            "posts/drafts/x.sh", "posts/drafts/a%2fb.md",
        ):
            with self.subTest(path=path), self.assertRaises(DraftError):
                validate_path(path)
        self.assertEqual(validate_path(PATH), PATH)
        self.assertEqual(validate_repo(REPO), REPO)
        for repo in ("a", "a/../b", "a/.git", "https://evil.com/a", "a/b/c"):
            with self.assertRaises(DraftError):
                validate_repo(repo)
        with self.assertRaises(DraftError):
            validate_content("x" * (MAX_FILE_BYTES + 1))
        with self.assertRaises(DraftError):
            validate_content("hello\x00world")

    def test_new_file_becomes_draft_pr_never_main(self):
        with tempfile.TemporaryDirectory() as raw:
            drafts, fake = self.service(Path(raw) / "drafts")
            result = drafts.write_file(PATH, "# Hello world\n\nA first draft.\n", "Hello world")
            self.assertEqual(result["status"], "draft_pr")
            self.assertTrue(result["requires_human_review"])
            self.assertFalse(result["merged"])
            self.assertTrue(result["draft_pr_url"].endswith("/pull/101"))
            self.assertTrue(result["branch"].startswith("wrkhorse-draft-"))
            self.assertEqual(drafts.read_file(PATH, result["draft_id"])["content"], "# Hello world\n\nA first draft.\n")
            self.assertTrue((Path(raw) / "drafts" / (result["draft_id"] + ".json")).is_file())
            self.assertNotIn(TOKEN, (Path(raw) / "drafts" / (result["draft_id"] + ".json")).read_text())
            self.assertEqual(len([c for c in fake.calls if c[:2] == ("POST", "/pulls")]), 1)
            for method, suffix, body in fake.calls:
                self.assertNotEqual(method, "DELETE")
                if method == "PUT":
                    self.assertNotEqual(body["branch"], "main")
                    self.assertTrue(body["branch"].startswith("wrkhorse-draft-"))

    def test_edit_requires_unique_exact_match_and_still_open_draft(self):
        with tempfile.TemporaryDirectory() as raw:
            drafts, fake = self.service(Path(raw))
            d = drafts.write_file(PATH, "# Post\n\nOld sentence.\n", "My post")
            draft_id = d["draft_id"]
            with self.assertRaisesRegex(DraftError, "old_text_must_match"):
                drafts.edit_file(draft_id, "not here", "new")
            self.assertEqual(fake.edit_commits, 1)
            updated = drafts.edit_file(draft_id, "Old sentence.", "New sentence.")
            self.assertEqual(updated["status"], "draft_updated")
            self.assertEqual(fake.edit_commits, 2)
            self.assertIn("New sentence.", drafts.read_file(PATH, draft_id)["content"])
            fake.ready = True
            with self.assertRaisesRegex(DraftError, "draft_pr_no_longer_editable"):
                drafts.edit_file(draft_id, "New", "Changed")
            fake.ready = False
            fake.closed = True
            with self.assertRaisesRegex(DraftError, "draft_pr_no_longer_editable"):
                drafts.edit_file(draft_id, "New", "Changed")
            self.assertEqual(fake.edit_commits, 2)

    def test_edit_ambiguity_and_unknown_ids_fail_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            drafts, fake = self.service(Path(raw))
            item = drafts.write_file(PATH, "Repeat Repeat", "Post")
            with self.assertRaisesRegex(DraftError, "old_text_must_match"):
                drafts.edit_file(item["draft_id"], "Repeat", "Other")
            with self.assertRaisesRegex(DraftError, "unknown_draft"):
                drafts.edit_file("../secrets", "Repeat", "Other")
            with self.assertRaisesRegex(DraftError, "draft_path_mismatch"):
                drafts.read_file("posts/drafts/other.md", item["draft_id"])
            self.assertEqual(fake.edit_commits, 1)

    def test_existing_main_path_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as raw:
            drafts, fake = self.service(Path(raw))
            fake.data[("main", PATH)] = "Existing content"
            with self.assertRaisesRegex(DraftError, "already_exists"):
                drafts.write_file(PATH, "Overwritten!", "Post")
            self.assertFalse(any(method == "POST" for method, _, _ in fake.calls))

    def test_config_is_opt_in_and_token_is_required(self):
        with tempfile.TemporaryDirectory() as raw:
            env = {
                "WRKHORSE_AUTH_TOKEN": MCP_TOKEN,
                "WRKHORSE_DB": str(Path(raw) / "db.sqlite"),
                "WRKHORSE_GITHUB_DRAFT_REPO": REPO,
                "WRKHORSE_GITHUB_TOKEN": TOKEN,
            }
            with patch.dict(os.environ, env, clear=True):
                settings = load_gateway_settings()
                self.assertIsNone(settings.github_drafts_repo)
                self.assertIsNone(settings.github_drafts_token)
            with patch.dict(os.environ, {**env, "WRKHORSE_GITHUB_DRAFTS_ENABLED": "1"}, clear=True):
                settings = load_gateway_settings()
                self.assertEqual(settings.github_drafts_repo, REPO)
                self.assertEqual(settings.github_drafts_token, TOKEN)
            for overrides in (
                {"WRKHORSE_GITHUB_TOKEN": ""},
                {"WRKHORSE_GITHUB_DRAFT_REPO": "https://github.com/noppanit/wrkhorse"},
                {"WRKHORSE_GITHUB_TOKEN": "not-a-real-token"},
            ):
                with patch.dict(os.environ, {
                    **env, "WRKHORSE_GITHUB_DRAFTS_ENABLED": "1", **overrides,
                }, clear=True), self.assertRaises(ConfigError):
                    load_gateway_settings()

    def test_github_secret_is_redacted_from_logs(self):
        import logging
        with patch.dict(os.environ, {"WRKHORSE_GITHUB_TOKEN": TOKEN}):
            record = logging.LogRecord("test", logging.INFO, __file__, 1, "key: %s", (TOKEN,), None)
            ScrubFilter().filter(record)
        self.assertNotIn(TOKEN, record.getMessage())


if __name__ == "__main__":
    unittest.main()
