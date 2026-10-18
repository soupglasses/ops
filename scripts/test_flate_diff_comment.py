# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
#
# SPDX-License-Identifier: 0BSD

"""Verify comment lifecycle without publishing to GitHub."""

import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "comment", Path(__file__).with_name("flate-diff-comment.py"),
)
comment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(comment)


def entry(identifier, body, author="github-actions[bot]"):
    return {"id": identifier, "body": body, "user": {"login": author}}


class CommentTests(unittest.TestCase):
    def publish(self, pages, failure=False, summary="## Rendered Flux Diff\n\n+changed"):
        def respond(endpoint, method="GET", body=None, paginate=False):
            if method == "GET":
                return pages
            if failure:
                raise subprocess.CalledProcessError(1, "gh")

        env = {
            "GITHUB_REPOSITORY": "soupglasses/ops", "PR_NUMBER": "42",
            "GITHUB_SERVER_URL": "https://github.com",
            "PR_HEAD_SHA": "abcdef1234567890abcdef1234567890abcdef12",
        }
        with patch.dict("os.environ", env), patch.object(
            Path, "read_text", return_value=summary,
        ), patch.object(comment, "api", side_effect=respond) as api:
            if failure:
                with self.assertRaises(subprocess.CalledProcessError):
                    comment.main()
            else:
                comment.main()
        return api.call_args_list

    def test_creates_first_comment(self):
        calls = self.publish([[]])
        self.assertTrue(calls[0].kwargs["paginate"])
        self.assertEqual(calls[1].args[:2], ("repos/soupglasses/ops/issues/42/comments", "POST"))
        self.assertIn(comment.MARKER, calls[1].args[2])
        self.assertIn("[`abcdef123456`]", calls[1].args[2])
        self.assertIn("/commit/abcdef1234567890abcdef1234567890abcdef12", calls[1].args[2])

    def test_updates_existing_comment(self):
        calls = self.publish([[entry(7, comment.MARKER + "\nold diff")]])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1].args[:2], ("repos/soupglasses/ops/issues/comments/7", "PATCH"))
        self.assertIn("+changed", calls[1].args[2])
        self.assertNotIn("old diff", calls[1].args[2])

    def test_no_diff_does_not_create_a_comment(self):
        for summary in ("No rendered Flux diff.\n", ""):
            calls = self.publish([[]], summary=summary)
            self.assertEqual(len(calls), 1)

    def test_no_diff_removes_previous_diff_comments_and_preserves_others(self):
        calls = self.publish([
            [entry(1, comment.MARKER), entry(2, "other bot report")],
            [entry(3, "## Rendered Flux Diff\nold"), entry(4, comment.MARKER, "sofie")],
        ], summary="No rendered Flux diff.\n")
        self.assertEqual([call.args[1] for call in calls[1:]], ["DELETE", "DELETE"])
        self.assertTrue(calls[1].args[0].endswith("/1"))
        self.assertTrue(calls[2].args[0].endswith("/3"))

    def test_consolidates_legacy_comments_across_pages_and_preserves_others(self):
        calls = self.publish([
            [entry(1, "## Rendered Flux Diff\nold"), entry(2, "other bot report")],
            [entry(3, comment.MARKER), entry(4, comment.MARKER, "sofie")],
        ])
        self.assertEqual([call.args[1] for call in calls[1:]], ["PATCH", "DELETE"])
        self.assertTrue(calls[1].args[0].endswith("/1"))
        self.assertTrue(calls[2].args[0].endswith("/3"))

    def test_failed_update_preserves_previous_comments(self):
        calls = self.publish([[entry(1, comment.MARKER), entry(2, comment.MARKER)]], failure=True)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
