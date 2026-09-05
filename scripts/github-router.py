#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Soup Bot <ops+bot@finnes.dev>
#
# SPDX-License-Identifier: 0BSD

"""Filter GitHub events and annotate explicit Soupbot routing commands."""

import json
import re
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
PR_ACTIONS = {"opened", "reopened", "ready_for_review", "synchronize"}
ISSUE_ACTIONS = {"opened", "edited", "reopened"}


def mention_target(body: str) -> str | None:
    match = MENTION.search(body)
    if not match:
        return None
    target = (match.group(1) or "opslead").lower()
    return target if target in PROFILES else "opslead"


def main() -> int:
    payload = json.load(sys.stdin)
    repository = payload.get("repository") or {}
    if repository.get("full_name") != "soupglasses/ops":
        return 0

    action = payload.get("action", "")
    sender = (payload.get("sender") or {}).get("login", "").lower()
    if sender in {"soupbot", "soupbot[bot]"}:
        return 0

    target = None
    reason = None

    if payload.get("comment") and payload.get("issue") and action == "created":
        target = mention_target(((payload.get("comment") or {}).get("body") or ""))
        reason = "explicit-mention"
    elif payload.get("issue") and not payload.get("pull_request") and action in ISSUE_ACTIONS:
        target = mention_target(((payload.get("issue") or {}).get("body") or ""))
        reason = "explicit-mention"
    elif payload.get("pull_request") and action in PR_ACTIONS:
        pull_request = payload.get("pull_request") or {}
        if pull_request.get("draft") and action != "ready_for_review":
            return 0
        target = mention_target(pull_request.get("body") or "") or "opslead"
        reason = "explicit-mention" if target != "opslead" else "pr-review-routing"
    else:
        return 0

    if target is None:
        return 0

    payload["_hermes"] = {"target_profile": target, "reason": reason}
    json.dump(payload, sys.stdout, separators=(",", ":"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
