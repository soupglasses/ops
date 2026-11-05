#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
#
# SPDX-License-Identifier: 0BSD

"""Keep editor Kubernetes schemas aligned with the RKE2 inventory."""

import argparse
from pathlib import Path
import re
import subprocess


HEADER = re.compile(
    r"(?m)^(# yaml-language-server: \$schema=https://raw\.githubusercontent\.com/"
    r"yannh/kubernetes-json-schema/[^/]+/)v\d+\.\d+\.\d+(?=[/-])"
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Update stale schema comments")
    args = parser.parse_args()
    root = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip())
    inventory = root / "ansible/inventories/home/group_vars/all/main.yml"
    match = re.search(r"(?m)^rke2_version: (v\d+\.\d+\.\d+)\+", inventory.read_text())
    if not match:
        parser.error("Cannot find the Kubernetes version in the RKE2 inventory")
    version = match[1]
    files = subprocess.check_output(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "kubernetes"], cwd=root)
    stale = []
    for filename in files.decode().split("\0"):
        path = Path(filename)
        if path.suffix not in {".yaml", ".yml"} or ".sops." in path.name:
            continue
        if not (root / path).is_file():
            continue
        source = (root / path).read_text()
        updated = HEADER.sub(lambda found: found[1] + version, source)
        if source != updated:
            stale.append(filename)
            if args.write:
                (root / path).write_text(updated)
            print(f"{filename}: {'updated to' if args.write else 'expected'} {version}")
    if stale and not args.write:
        print("Run mise exec -- task schemas:update and commit the updated schema comments.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
