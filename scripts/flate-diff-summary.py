#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
#
# SPDX-License-Identifier: 0BSD

"""Build a bounded PR comment from Flate's readable and unified diffs."""

import argparse
import re
from pathlib import Path


LIMIT = 60_000


def summarize(unified):
    # Flate labels each unified diff with its rendered resource identity.
    resources = []
    current = None
    for line in unified.splitlines():
        if line.startswith("--- "):
            current = [line[4:], 0, 0]
            resources.append(current)
        elif current is not None and not line.startswith("+++ "):
            if line.startswith("+"):
                current[1] += 1
            elif line.startswith("-"):
                current[2] += 1
    return resources


def render(readable, unified, artifact_url):
    resources = summarize(unified)
    if not resources:
        return "No rendered Flux diff.\n"

    added = sum(row[1] for row in resources)
    removed = sum(row[2] for row in resources)
    overview = (
        "## Rendered Flux Diff\n\n"
        "### Overview\n\n"
        f"**{len(resources):,} rendered resources, {added:,} lines added, "
        f"{removed:,} lines removed.**\n\n"
        "Counts compare serialized rendered YAML, including list reordering.\n\n"
        f"[Download the complete rendered diff]({artifact_url}).\n\n"
        "| Rendered resource | Added | Removed |\n"
        "| --- | ---: | ---: |\n"
    )
    # Leave room for the fallback notice even when the resource list is huge.
    for index, (name, additions, deletions) in enumerate(resources):
        name = name.replace("|", "&#124;").replace("`", "&#96;")
        row = f"| `{name}` | {additions:,} | {deletions:,} |\n"
        if len((overview + row).encode()) > LIMIT - 1_000:
            overview += f"\n{len(resources) - index:,} more resources in the complete diff.\n"
            break
        overview += row

    # A longer fence keeps manifest text containing backticks inside the diff.
    fence = "`" * max(3, max(map(len, re.findall(r"`+", readable)), default=0) + 1)
    inline = overview + f"\n{fence}diff\n{readable.rstrip()}\n{fence}\n"
    if len(inline.encode()) <= LIMIT:
        return inline
    return overview + "\nThe rendered diff exceeds the inline size limit.\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("readable", type=Path)
    parser.add_argument("unified", type=Path)
    parser.add_argument("artifact_url")
    args = parser.parse_args()
    print(render(args.readable.read_text(), args.unified.read_text(), args.artifact_url), end="")


if __name__ == "__main__":
    main()
