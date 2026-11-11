"""Read-only workspace file tools. No network and no code execution."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx2
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from wrkhorse.config import ConfigError, load_gateway_settings
from wrkhorse.local_files import (
    HARD_MAX_BYTES,
    MAX_READ_BYTES,
    DISABLED_MESSAGE,
    FilesError,
    LocalFiles,
    parse_files_root,
)
from wrkhorse.server import build_server, http_app

TOKEN = "f" * 48
GITHUB_TOKEN = "github_pat_" + "g" * 35


def _error(fn) -> str:
    try:
        fn()
    except FilesError as exc:
        return str(exc)
    raise AssertionError("expected FilesError")


class LocalFilePolicyTests(unittest.TestCase):
    def test_disabled_when_root_is_unset(self):
        files = LocalFiles(None)
        self.assertEqual(_error(lambda: files.read_file("/etc/passwd")), DISABLED_MESSAGE)
        self.assertEqual(_error(lambda: files.list_files("..")), DISABLED_MESSAGE)
        self.assertEqual(_error(lambda: files.read_file("id_rsa")), DISABLED_MESSAGE)

    def test_traversal_and_absolute_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "root"
            root.mkdir()
            (root / "ok.txt").write_text("hello\n", encoding="utf-8")
            outside = Path(raw) / "secret.txt"
            outside.write_text("nope\n", encoding="utf-8")
            files = LocalFiles(root.resolve())
            for path in (
                "../secret.txt",
                "..",
                "/etc/passwd",
                str(root / "ok.txt"),
                "foo/../../secret.txt",
                "~/ok.txt",
                "ok.txt\x00",
                "subdir\\..\\secret.txt",
            ):
                self.assertEqual(
                    _error(lambda path=path: files.read_file(path)),
                    "path is outside the files root",
                    path,
                )
                self.assertNotIn("nope", _error(lambda path=path: files.read_file(path)))

    def test_symlink_escape_is_rejected_and_inside_symlink_is_readable(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "root"
            outside = Path(raw) / "outside"
            root.mkdir()
            outside.mkdir()
            (root / "ok.txt").write_text("safe\n", encoding="utf-8")
            (root / "real.txt").write_text("inside\n", encoding="utf-8")
            (outside / "secret.txt").write_text("outside-secret\n", encoding="utf-8")
            (root / "escape").symlink_to(outside / "secret.txt")
            (root / "escape_rel").symlink_to(Path("..") / "outside" / "secret.txt")
            (root / "escape_dir").symlink_to(outside)
            (root / "alias.txt").symlink_to(root / "real.txt")
            files = LocalFiles(root.resolve())

            self.assertEqual(_error(lambda: files.read_file("escape")), "path is outside the files root")
            self.assertEqual(
                _error(lambda: files.read_file("escape_rel")),
                "path is outside the files root",
            )
            self.assertEqual(
                _error(lambda: files.read_file("escape_dir/secret.txt")),
                "path is outside the files root",
            )
            self.assertEqual(
                _error(lambda: files.list_files("escape_dir")),
                "path is outside the files root",
            )
            listed = files.list_files(".")
            names = [entry["name"] for entry in listed["entries"]]
            self.assertIn("ok.txt", names)
            self.assertIn("alias.txt", names)
            self.assertNotIn("escape", names)
            self.assertNotIn("escape_dir", names)
            self.assertNotIn("secret.txt", names)
            self.assertNotIn("outside-secret", json.dumps(listed))

            body = files.read_file("alias.txt")
            self.assertEqual(body["content"], "inside\n")
            self.assertFalse(body["executes_code"])
            self.assertFalse(body["truncated"])

    def test_denylist_blocks_secrets_and_listing_skips_them(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "root"
            root.mkdir()
            secret = "super-secret-token-value"
            blocked = {
                ".env": "TOKEN=" + secret,
                ".env.local": "LOCAL=" + secret,
                ".netrc": "machine " + secret,
                "id_rsa": secret,
                "id_ed25519": secret,
                "id_ecdsa.pub": secret,
                "cert.pem": secret,
                "token.key": secret,
                "credentials.json": secret,
                "Login.keychain-db": secret,
            }
            for name, text in blocked.items():
                (root / name).write_text(text, encoding="utf-8")
            for directory in (".ssh", ".aws", ".gnupg", ".git"):
                folder = root / directory
                folder.mkdir()
                (folder / "config").write_text(secret, encoding="utf-8")
            (root / "notes.py").write_text("print('ok')\n", encoding="utf-8")
            (root / "alias-env").symlink_to(root / ".env")
            files = LocalFiles(root.resolve())

            for name in list(blocked) + [
                ".ssh/config",
                ".aws/config",
                ".gnupg/config",
                ".git/config",
                ".git/HEAD",
                "alias-env",
            ]:
                message = _error(lambda name=name: files.read_file(name))
                self.assertEqual(message, "path is blocked", name)
                self.assertNotIn(secret, message)

            listed = files.list_files(".", recursive=True, max_entries=100)
            names = [entry["name"] for entry in listed["entries"]]
            self.assertEqual(names, ["notes.py"])
            self.assertNotIn(secret, json.dumps(listed))
            self.assertEqual(_error(lambda: files.list_files(".ssh")), "path is blocked")
            self.assertEqual(_error(lambda: files.list_files(".git")), "path is blocked")

    def test_blocked_directory_cannot_be_the_root(self):
        with tempfile.TemporaryDirectory() as raw:
            ssh = Path(raw) / ".ssh"
            ssh.mkdir()
            (ssh / "config").write_text("Host *\n", encoding="utf-8")
            files = LocalFiles(ssh.resolve())
            self.assertEqual(_error(lambda: files.read_file("config")), "path is blocked")
            self.assertEqual(_error(lambda: files.list_files(".")), "path is blocked")
            with self.assertRaises(Exception) as caught:
                parse_files_root(str(ssh))
            self.assertIn("blocked secrets directory", str(caught.exception))

    def test_binary_is_refused_and_text_is_not_executed(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "root"
            root.mkdir()
            marker = Path(raw) / "executed"
            script = root / "run.sh"
            script.write_text("#!/bin/sh\ntouch " + str(marker) + "\n", encoding="utf-8")
            script.chmod(0o755)
            before = script.stat()
            (root / "blob.bin").write_bytes(b"\x00\x01\x02not-text")
            (root / "bad.txt").write_bytes(b"\xff\xfe not utf-8")
            files = LocalFiles(root.resolve())

            body = files.read_file("run.sh")
            self.assertIn("touch", body["content"])
            self.assertFalse(marker.exists())
            self.assertEqual(script.stat().st_mode, before.st_mode)
            self.assertEqual(script.stat().st_mtime_ns, before.st_mtime_ns)
            self.assertTrue(stat.S_ISREG(script.stat().st_mode))
            self.assertEqual(_error(lambda: files.read_file("blob.bin")), "binary file refused")
            self.assertEqual(_error(lambda: files.read_file("bad.txt")), "binary file refused")
            self.assertEqual(_error(lambda: files.read_file("missing.txt")), "not found")
            self.assertEqual(_error(lambda: files.read_file(".")), "not a file")
            self.assertEqual(_error(lambda: files.list_files("run.sh")), "not a directory")

    def test_line_paging_and_size_cap(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "root"
            root.mkdir()
            text = "".join(f"line-{i}\n" for i in range(1, 11))
            (root / "lines.txt").write_text(text, encoding="utf-8")
            wide = "".join(("y" * 1000) + "\n" for _ in range(300))
            (root / "wide.txt").write_text(wide, encoding="utf-8")
            (root / "huge-line.txt").write_text("z" * (MAX_READ_BYTES + 1), encoding="utf-8")
            (root / "over-hard.txt").write_bytes(b"a" * (HARD_MAX_BYTES + 1))
            files = LocalFiles(root.resolve())

            page = files.read_file("lines.txt", offset=3, limit=2)
            self.assertEqual(page["content"], "line-3\nline-4\n")
            self.assertEqual(page["total_lines"], 10)
            self.assertEqual(page["lines_returned"], 2)
            self.assertTrue(page["truncated"])
            self.assertEqual(page["offset"], 3)

            tail = files.read_file("lines.txt", offset=9, limit=10)
            self.assertEqual(tail["content"], "line-9\nline-10\n")
            self.assertFalse(tail["truncated"])
            self.assertEqual(tail["total_lines"], 10)

            past = files.read_file("lines.txt", offset=40, limit=2)
            self.assertEqual(past["content"], "")
            self.assertEqual(past["total_lines"], 10)
            self.assertFalse(past["truncated"])

            whole = files.read_file("lines.txt")
            self.assertEqual(whole["content"], text)
            self.assertFalse(whole["truncated"])
            self.assertEqual(whole["total_lines"], 10)

            window = files.read_file("wide.txt", offset=1, limit=10)
            self.assertEqual(window["lines_returned"], 10)
            self.assertEqual(window["total_lines"], 300)
            self.assertTrue(window["truncated"])
            self.assertLessEqual(len(window["content"].encode("utf-8")), MAX_READ_BYTES)

            capped = files.read_file("wide.txt", offset=1, limit=5000)
            self.assertTrue(capped["truncated"])
            self.assertLessEqual(len(capped["content"].encode("utf-8")), MAX_READ_BYTES)
            self.assertGreater(capped["lines_returned"], 0)

            self.assertEqual(_error(lambda: files.read_file("huge-line.txt")), "file is too large")
            self.assertEqual(_error(lambda: files.read_file("over-hard.txt")), "file is too large")

    def test_list_reports_type_size_and_caps_entries(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "root"
            nested = root / "src"
            nested.mkdir(parents=True)
            payload = "abc\n"
            (nested / "app.py").write_text(payload, encoding="utf-8")
            (root / "README.md").write_text("hi\n", encoding="utf-8")
            files = LocalFiles(root.resolve())
            top = files.list_files(".")
            by_name = {entry["name"]: entry for entry in top["entries"]}
            self.assertEqual(set(by_name), {"README.md", "src"})
            self.assertEqual(by_name["README.md"]["type"], "file")
            self.assertEqual(by_name["README.md"]["size"], len("hi\n".encode("utf-8")))
            self.assertEqual(by_name["src"]["type"], "dir")
            self.assertEqual(by_name["src"]["size"], 0)
            self.assertFalse(top["truncated"])

            deep = files.list_files("src")
            self.assertEqual(deep["entries"], [{
                "name": "src/app.py",
                "type": "file",
                "size": len(payload.encode("utf-8")),
            }])
            capped = files.list_files(".", recursive=True, max_entries=1)
            self.assertEqual(capped["count"], 1)
            self.assertTrue(capped["truncated"])
            self.assertFalse(capped["executes_code"])


class FilesRootConfigTests(unittest.TestCase):
    def test_unset_is_disabled_and_invalid_roots_fail(self):
        self.assertIsNone(parse_files_root(None))
        self.assertIsNone(parse_files_root(""))
        self.assertIsNone(parse_files_root("   "))
        with self.assertRaisesRegex(Exception, "absolute path"):
            parse_files_root("relative/dir")
        with self.assertRaisesRegex(Exception, "whitespace"):
            parse_files_root(" /tmp ")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace"
            root.mkdir()
            (root / "note.txt").write_text("note\n", encoding="utf-8")
            link = Path(raw) / "link"
            link.symlink_to(root)
            self.assertEqual(parse_files_root(str(link)), root.resolve())
            missing = Path(raw) / "missing"
            with self.assertRaisesRegex(Exception, "does not exist"):
                parse_files_root(str(missing))
            file_path = root / "note.txt"
            with self.assertRaisesRegex(Exception, "must be a directory"):
                parse_files_root(str(file_path))

            env = {
                "WRKHORSE_AUTH_TOKEN": TOKEN,
                "WRKHORSE_DB": str(Path(raw) / "q.sqlite"),
            }
            with patch.dict(os.environ, env, clear=True):
                self.assertIsNone(load_gateway_settings().files_root)
            with patch.dict(os.environ, {**env, "WRKHORSE_FILES_ROOT": str(root)}, clear=True):
                self.assertEqual(load_gateway_settings().files_root, root.resolve())
            with patch.dict(os.environ, {**env, "WRKHORSE_FILES_ROOT": "repo"}, clear=True):
                with self.assertRaises(ConfigError):
                    load_gateway_settings()


class WorkspaceToolRegistrationTests(unittest.TestCase):
    def _server(self, env: dict):
        with patch.dict(os.environ, env, clear=True):
            settings = load_gateway_settings()
        return build_server(settings), settings

    def test_unset_root_registers_tools_that_report_disabled(self):
        with tempfile.TemporaryDirectory() as raw:
            server, _settings = self._server({
                "WRKHORSE_AUTH_TOKEN": TOKEN,
                "WRKHORSE_DB": str(Path(raw) / "q.sqlite"),
            })
            tools = {tool.name: tool for tool in server._tool_manager.list_tools()}
            self.assertIn("list_files", tools)
            self.assertIn("read_file", tools)
            self.assertTrue(tools["read_file"].annotations.read_only_hint)
            self.assertFalse(tools["read_file"].annotations.destructive_hint)
            with self.assertRaisesRegex(Exception, "disabled"):
                tools["read_file"].fn("README.md")
            with self.assertRaisesRegex(Exception, "disabled"):
                tools["list_files"].fn(".")
            status = tools["get_project_status"].fn()
            self.assertIn("list_files", status["tools"])
            self.assertIn("read_file", status["tools"])
            self.assertFalse(status["local_files"]["enabled"])
            self.assertEqual(status["local_files"]["reason"], "WRKHORSE_FILES_ROOT is unset")
            self.assertIn(".ssh/", status["local_files"]["denylist"])
            self.assertFalse(status["local_files"]["executes_code"])
            self.assertNotIn(str(Path(raw)), json.dumps(status))

    def test_github_draft_read_keeps_its_name_until_files_root_is_set(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "root"
            root.mkdir()
            (root / "ok.txt").write_text("local-text\n", encoding="utf-8")
            base = {
                "WRKHORSE_AUTH_TOKEN": TOKEN,
                "WRKHORSE_DB": str(Path(raw) / "q.sqlite"),
                "WRKHORSE_GITHUB_DRAFTS_ENABLED": "1",
                "WRKHORSE_GITHUB_DRAFT_REPO": "noppanit/wrkhorse",
                "WRKHORSE_GITHUB_TOKEN": GITHUB_TOKEN,
            }
            drafts_only, _settings = self._server(base)
            names = {tool.name for tool in drafts_only._tool_manager.list_tools()}
            self.assertIn("read_file", names)
            self.assertIn("list_files", names)
            self.assertNotIn("read_draft_file", names)
            listed = {tool.name: tool for tool in drafts_only._tool_manager.list_tools()}
            with self.assertRaisesRegex(Exception, "disabled"):
                listed["list_files"].fn(".")
            with self.assertRaisesRegex(Exception, "only_posts_drafts"):
                listed["read_file"].fn("ok.txt")

            both, _settings = self._server({**base, "WRKHORSE_FILES_ROOT": str(root)})
            tools = {tool.name: tool for tool in both._tool_manager.list_tools()}
            self.assertIn("read_draft_file", tools)
            self.assertIn("write_file", tools)
            self.assertIn("edit_file", tools)
            body = tools["read_file"].fn("ok.txt")
            self.assertEqual(body["content"], "local-text\n")
            self.assertFalse(body["executes_code"])
            status = tools["get_project_status"].fn()
            self.assertIn("list_files", status["tools"])
            self.assertIn("read_file", status["tools"])
            self.assertIn("read_draft_file", status["tools"])
            self.assertEqual(status["github_drafts"]["read_tool"], "read_draft_file")
            self.assertTrue(status["local_files"]["enabled"])
            self.assertEqual(status["tools"].count("read_file"), 1)
            self.assertNotIn(str(root), json.dumps(status))


class WorkspaceFileMCPTests(unittest.TestCase):
    def test_authenticated_client_can_list_and_page(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "root"
            (root / "pkg").mkdir(parents=True)
            (root / "pkg" / "app.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
            (root / ".env").write_text("SECRET=1\n", encoding="utf-8")
            env = {
                "WRKHORSE_AUTH_TOKEN": TOKEN,
                "WRKHORSE_DB": str(Path(raw) / "q.sqlite"),
                "WRKHORSE_FILES_ROOT": str(root),
            }
            with patch.dict(os.environ, env, clear=True):
                settings = load_gateway_settings(host="127.0.0.1", port=0)
            app = http_app(settings)
            config = uvicorn.Config(
                app, host="127.0.0.1", port=0, access_log=False, log_level="warning"
            )
            server = uvicorn.Server(config)
            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()
            deadline = time.monotonic() + 10
            while not server.started and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(server.started)
            port = [sock for item in server.servers for sock in item.sockets][0].getsockname()[1]

            async def call():
                async with httpx2.AsyncClient(
                    headers={"Authorization": f"Bearer {TOKEN}"},
                    timeout=15,
                    trust_env=False,
                ) as http:
                    async with streamable_http_client(
                        f"http://127.0.0.1:{port}/mcp",
                        http_client=http,
                    ) as (read, write):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            names = {tool.name for tool in (await session.list_tools()).tools}
                            status = await session.call_tool("get_project_status", {})
                            listed = await session.call_tool(
                                "list_files", {"path": ".", "recursive": True}
                            )
                            page = await session.call_tool(
                                "read_file",
                                {"path": "pkg/app.py", "offset": 2, "limit": 1},
                            )
                            blocked = await session.call_tool("read_file", {"path": ".env"})
                            escaped = await session.call_tool(
                                "read_file", {"path": "../secret"}
                            )
                            return names, status, listed, page, blocked, escaped

            try:
                names, status, listed, page, blocked, escaped = asyncio.run(call())
            finally:
                server.should_exit = True
                thread.join(timeout=5)

            self.assertTrue({"list_files", "read_file", "get_project_status"}.issubset(names))
            project = json.loads(status.content[0].text)
            self.assertTrue(project["local_files"]["enabled"])
            self.assertIn("list_files", project["tools"])
            self.assertIn("read_file", project["tools"])
            entries = json.loads(listed.content[0].text)
            self.assertFalse(listed.is_error)
            self.assertEqual(
                [entry["name"] for entry in entries["entries"]],
                ["pkg", "pkg/app.py"],
            )
            body = json.loads(page.content[0].text)
            self.assertEqual(body["content"], "two\n")
            self.assertEqual(body["total_lines"], 3)
            self.assertTrue(body["truncated"])
            self.assertFalse(body["executes_code"])
            self.assertTrue(blocked.is_error)
            self.assertIn("blocked", blocked.content[0].text)
            self.assertNotIn("SECRET", blocked.content[0].text)
            self.assertTrue(escaped.is_error)
            self.assertIn("outside", escaped.content[0].text)
            self.assertNotIn("../secret", escaped.content[0].text)


if __name__ == "__main__":
    unittest.main()
