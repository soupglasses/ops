#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Soup Bot <ops+bot@finnes.dev>
#
# SPDX-License-Identifier: 0BSD

"""Regression checks for the GitHub webhook routing policy."""

import importlib.util
import io
import json
import pathlib
import sys
import unittest
from unittest.mock import patch

SCRIPT = pathlib.Path(__file__).with_name("github-router.py")
SPEC = importlib.util.spec_from_file_location("github_router", SCRIPT)
assert SPEC and SPEC.loader
ROUTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ROUTER)


def route(payload: dict) -> dict | None:
    stdin = io.StringIO(json.dumps(payload))
    stdout = io.StringIO()
    with (
        patch.object(sys, "stdin", stdin),
        patch.object(sys, "stdout", stdout),
        patch.object(ROUTER, "sender_is_trusted", return_value=True),
    ):
        ROUTER.main()
    return json.loads(stdout.getvalue()) if stdout.getvalue() else None


def base(**values) -> dict:
    payload = {
        "action": "opened",
        "repository": {"full_name": "soupglasses/ops"},
        "sender": {"login": "human"},
    }
    payload.update(values)
    return payload


class RoutingPolicyTest(unittest.TestCase):
    def test_new_human_issue_routes_to_opslead(self):
        result = route(base(issue={"number": 1, "body": "No mention"}))
        self.assertEqual(result["_hermes"], {"target_profile": "opslead", "reason": "new-issue"})

    def test_soupbot_issue_is_ignored(self):
        self.assertIsNone(route(base(sender={"login": "soupbot[bot]"}, issue={"number": 1})))

    def test_untrusted_sender_is_ignored_for_every_event(self):
        payloads = [
            base(issue={"number": 1, "body": "@soupbot"}),
            base(
                action="created",
                issue={"number": 1},
                comment={"body": "@soupbot security"},
            ),
            base(pull_request={"number": 2, "draft": False, "body": "@soupbot"}),
        ]
        for payload in payloads:
            stdin = io.StringIO(json.dumps(payload))
            stdout = io.StringIO()
            with (
                self.subTest(payload=payload),
                patch.object(sys, "stdin", stdin),
                patch.object(sys, "stdout", stdout),
                patch.object(ROUTER, "sender_is_trusted", return_value=False),
            ):
                ROUTER.main()
                self.assertEqual(stdout.getvalue(), "")

    def test_unmentioned_issue_comment_is_ignored(self):
        payload = base(action="created", issue={"number": 1}, comment={"body": "hello"})
        self.assertIsNone(route(payload))

    def test_issue_comment_mention_routes_named_profile(self):
        payload = base(
            action="created",
            issue={"number": 1},
            comment={"body": "@soupbot security review this"},
        )
        self.assertEqual(route(payload)["_hermes"]["target_profile"], "security")

    def test_new_ready_pr_routes_to_opslead(self):
        payload = base(pull_request={"number": 2, "draft": False, "body": ""})
        self.assertEqual(route(payload)["_hermes"]["reason"], "new-pr")

    def test_draft_pr_is_ignored_until_ready(self):
        payload = base(pull_request={"number": 2, "draft": True, "body": ""})
        self.assertIsNone(route(payload))
        payload["action"] = "ready_for_review"
        self.assertEqual(route(payload)["_hermes"]["reason"], "pr-ready")

    def test_pr_synchronize_is_ignored(self):
        payload = base(action="synchronize", pull_request={"number": 2, "draft": False})
        self.assertIsNone(route(payload))

    def test_review_request_only_routes_when_soupbot_is_requested(self):
        payload = base(
            action="review_requested",
            pull_request={"number": 2, "draft": False},
            requested_reviewer={"login": "someone-else"},
        )
        self.assertIsNone(route(payload))
        payload["requested_reviewer"] = {"login": "soupbot[bot]"}
        self.assertEqual(route(payload)["_hermes"]["reason"], "review-request")

    def test_edited_pr_body_mention_routes_named_profile(self):
        payload = base(
            action="edited",
            pull_request={"number": 2, "draft": False, "body": "@soupbot resilience"},
        )
        self.assertEqual(route(payload)["_hermes"]["target_profile"], "resilience")

    def test_inline_review_comment_requires_mention(self):
        payload = base(
            action="created",
            pull_request={"number": 2},
            comment={"body": "please change this"},
        )
        self.assertIsNone(route(payload))
        payload["comment"]["body"] = "@soupbot incident investigate"
        self.assertEqual(route(payload)["_hermes"]["target_profile"], "incident")

    def test_review_body_requires_mention(self):
        payload = base(
            action="submitted",
            pull_request={"number": 2},
            review={"body": "looks good"},
        )
        self.assertIsNone(route(payload))
        payload["review"]["body"] = "@soupbot observability check this"
        self.assertEqual(route(payload)["_hermes"]["target_profile"], "observability")


if __name__ == "__main__":
    unittest.main()
