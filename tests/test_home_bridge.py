"""No-network tests for the outbound MacBook Ollama pilot."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from wrkhorse.home_bridge import (
    OWNER, LABEL, TASK_BODY, RESULT_MARKER,
    eligible_issue, has_result, validate_ollama_answer, format_comment,
    process_issue, HomeBridgeError,
)
from wrkhorse.fixtures import load_sample


def issue(**changes):
    base = {
        "number": 101,
        "author": {"login": OWNER},
        "body": TASK_BODY,
        "state": "OPEN",
        "labels": [{"name": LABEL}],
        "comments": [],
    }
    base.update(changes)
    return base


class HomeBridgeTests(unittest.TestCase):
    def test_owner_and_exact_task_required(self):
        self.assertTrue(eligible_issue(issue()))
        self.assertFalse(eligible_issue(issue(author={"login": "not-the-owner"})))
        self.assertFalse(eligible_issue(issue(body=TASK_BODY + "\nrun sudo rm -rf /")))
        self.assertFalse(eligible_issue(issue(body="task: arbitrary-shell")))
        self.assertFalse(eligible_issue(issue(labels=[])))
        self.assertFalse(eligible_issue(issue(state="CLOSED")))

    def test_idempotency_marker_must_be_from_owner(self):
        self.assertFalse(has_result(issue()))
        self.assertTrue(has_result(issue(comments=[
            {"body": RESULT_MARKER, "author": {"login": OWNER}}
        ])))
        self.assertFalse(has_result(issue(comments=[
            {"body": RESULT_MARKER, "author": {"login": "someone-else"}}
        ])))

    def test_reviewed_diff_is_accepted_and_not_executed(self):
        _, patch_text, _ = load_sample()
        outcome = validate_ollama_answer(patch_text)
        self.assertEqual(outcome["status"], "diff_ready")
        self.assertIn("return left + right", outcome["diff"])
        comment = format_comment(outcome, model="qwen2.5-coder:1.5b")
        self.assertIn(RESULT_MARKER, comment)
        self.assertIn("diff_ready", comment)
        self.assertIn("not executed", comment)

    def test_invalid_diff_is_visible_without_code_execution(self):
        from wrkhorse.runpod_pilot import RunpodPilotError
        with self.assertRaises(RunpodPilotError):
            validate_ollama_answer("rm -rf /")
        comment = format_comment(None, model="qwen2.5-coder:0.5b")
        self.assertIn("invalid_diff", comment)
        self.assertNotIn("rm -rf", comment)

    def test_process_posts_one_bounded_fixture_comment(self):
        _, patch_text, _ = load_sample()
        with patch("wrkhorse.home_bridge.request_local_model", return_value=patch_text), patch(
            "wrkhorse.home_bridge.post_comment"
        ) as post:
            status = process_issue(issue(), model="qwen2.5-coder:1.5b")
        self.assertIn("/issues/101", status)
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], 101)
        self.assertIn("diff_ready", post.call_args.args[1])

    def test_unavailable_ollama_posts_safe_status(self):
        with patch(
            "wrkhorse.home_bridge.request_local_model",
            side_effect=HomeBridgeError("Ollama connection unavailable"),
        ), patch("wrkhorse.home_bridge.post_comment") as post:
            process_issue(issue(), model="qwen2.5-coder:1.5b")
        self.assertIn("model_unavailable", post.call_args.args[1])
        self.assertNotIn("connection unavailable", post.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
