#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Soup Bot <bot+ops@finnes.dev>
#
# SPDX-License-Identifier: 0BSD

"""Filter GitHub events and annotate explicit Soupbot routing commands."""

import json
import re
import subprocess
import sys

PROFILES = {
    "opslead",
    "incident",
    "observability",
    "implementer",
    "security",
    "resilience",
}
MENTION = re.compile(
    r"(?<![\w-])@soupbot(?:[\s,:/-]+(?:to[\s,:/-]+)?"
    r"(opslead|incident|observability|implementer|security|resilience))?\b",
    re.IGNORECASE,
)
PR_CREATION_ACTIONS = {"opened", "reopened"}
ISSUE_CREATION_ACTIONS = {"opened"}
SOUPBOT_LOGINS = {"soupbot", "soupbot[bot]"}
TRUSTED_PERMISSIONS = {"admin", "maintain", "write"}


def mention_target(body: str) -> str | None:
    match = MENTION.search(body)
    if not match:
        return None
    target = (match.group(1) or "opslead").lower()
    return target if target in PROFILES else "opslead"


def sender_is_trusted(login: str) -> bool:
    """Fail closed unless GitHub reports collaborator write access or stronger."""
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", login):
        return False
    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                f"repos/soupglasses/ops/collaborators/{login}/permission",
                "--jq",
                ".permission",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.stdout.strip().lower() in TRUSTED_PERMISSIONS


def main() -> int:
    payload = json.load(sys.stdin)
    repository = payload.get("repository") or {}
    if repository.get("full_name") != "soupglasses/ops":
        return 0

    action = payload.get("action", "")
    sender = (payload.get("sender") or {}).get("login", "").lower()
    if sender in SOUPBOT_LOGINS:
        return 0
    if not sender_is_trusted(sender):
        return 0

    target = None
    reason = None

    issue = payload.get("issue") or {}
    pull_request = payload.get("pull_request") or {}
    comment = payload.get("comment") or {}
    review = payload.get("review") or {}

    # Conversation comments on issues and PRs route only explicit pings.
    if comment and issue and action == "created":
        target = mention_target(comment.get("body") or "")
        reason = "explicit-mention"
    # Inline pull-request review comments also route only explicit pings.
    elif comment and pull_request and action == "created":
        target = mention_target(comment.get("body") or "")
        reason = "explicit-mention"
    # A submitted review can contain a routing command in its summary body.
    elif review and pull_request and action == "submitted":
        target = mention_target(review.get("body") or "")
        reason = "explicit-mention"
    elif issue and not pull_request:
        body_target = mention_target(issue.get("body") or "")
        if action in ISSUE_CREATION_ACTIONS:
            target = body_target or "opslead"
            reason = "explicit-mention" if body_target else "new-issue"
        elif action == "edited" and body_target:
            target = body_target
            reason = "explicit-mention"
    elif pull_request:
        body_target = mention_target(pull_request.get("body") or "")
        if action in PR_CREATION_ACTIONS:
            if pull_request.get("draft"):
                return 0
            target = body_target or "opslead"
            reason = "explicit-mention" if body_target else "new-pr"
        elif action == "ready_for_review":
            target = body_target or "opslead"
            reason = "explicit-mention" if body_target else "pr-ready"
        elif action == "edited" and body_target:
            target = body_target
            reason = "explicit-mention"
        elif action == "review_requested":
            requested = (payload.get("requested_reviewer") or {}).get("login", "").lower()
            if requested in SOUPBOT_LOGINS:
                target = body_target or "opslead"
                reason = "review-request"
    else:
        return 0

    if target is None:
        return 0

    item_number = pull_request.get("number") or issue.get("number")
    if not isinstance(item_number, int) or item_number < 1:
        return 0
    payload["_hermes"] = {
        "target_profile": target,
        "reason": reason,
        "item_number": item_number,
    }
    json.dump(payload, sys.stdout, separators=(",", ":"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
