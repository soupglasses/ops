#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
#
# SPDX-License-Identifier: 0BSD

"""Keep one current rendered Flux diff comment on a pull request."""

import json
import os
from pathlib import Path
import subprocess


MARKER = "<!-- ops:flate-diff -->"


def api(endpoint, method="GET", body=None, paginate=False):
    command = ["gh", "api", endpoint, "--method", method]
    if paginate:
        command += ["--paginate", "--slurp"]
    if body is not None:
        command += ["--input", "-"]
    result = subprocess.run(
        command, input=json.dumps({"body": body}) if body is not None else None,
        text=True, capture_output=True, check=True,
    )
    return json.loads(result.stdout) if result.stdout.strip() else None


def main():
    repo = os.environ["GITHUB_REPOSITORY"]
    number = os.environ["PR_NUMBER"]
    run_url = f"{os.environ['GITHUB_SERVER_URL']}/{repo}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    body = f"{MARKER}\n{Path('body.md').read_text().rstrip()}\n\n[Latest validation run]({run_url}).\n"
    endpoint = f"repos/{repo}/issues/{number}/comments"
    pages = api(endpoint, paginate=True)
    # Recognize the old heading to consolidate comments created before the marker.
    comments = [
        comment for page in pages for comment in page
        if comment["user"]["login"] == "github-actions[bot]"
        and (comment["body"].startswith(MARKER)
             or comment["body"].startswith("## Rendered Flux Diff\n"))
    ]
    if comments:
        api(f"repos/{repo}/issues/comments/{comments[0]['id']}", "PATCH", body)
    else:
        api(endpoint, "POST", body)
    # Only remove old copies after the current result has been published.
    for comment in comments[1:]:
        api(f"repos/{repo}/issues/comments/{comment['id']}", "DELETE")


if __name__ == "__main__":
    main()
