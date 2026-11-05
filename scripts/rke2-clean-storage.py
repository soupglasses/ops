#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
#
# SPDX-License-Identifier: 0BSD
"""Remove only inventoried OpenEBS volumes from an uninstalled cluster."""

import argparse
import json
import re
import os
from pathlib import Path
import shutil
import subprocess
import sys

PVC = re.compile(r"pvc-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")


def command(*args):
    return subprocess.run(args, check=True, text=True, capture_output=True).stdout


def stopped():
    for service in ("rke2-server", "rke2-agent"):
        state = subprocess.run(
            ["systemctl", "is-active", service], text=True, capture_output=True
        ).stdout.strip()
        if state not in ("inactive", "failed", "unknown"):
            raise ValueError(f"Refusing storage removal: {service} is {state!r}")
    # A stopped service alone does not prove that kubelet/container workloads exited.
    if subprocess.run(["pgrep", "-x", "kubelet"], capture_output=True).returncode != 1:
        raise ValueError("Kubelet is running or its process check failed; uninstall RKE2 first")


def inventory(pool, parent):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", pool) or parent != pool + "/k8s":
        raise ValueError("Expected a pool name and its dedicated /k8s dataset")
    rows = {}
    output = command(
        "zfs", "list", "-H", "-p", "-r", "-t", "filesystem,volume,snapshot",
        "-o", "name,guid,type,mounted,origin", pool,
    )
    for line in output.splitlines():
        name, guid, kind, mounted, origin = line.split("\t")
        rows[name] = dict(guid=guid, type=kind, mounted=mounted, origin=origin)
    for required in (pool, parent):
        if rows.get(required, {}).get("type") != "filesystem":
            raise ValueError(f"Required storage filesystem is missing: {required}")
    roots = []
    for name, row in rows.items():
        if row["type"] == "snapshot":
            continue
        directory, _, leaf = name.rpartition("/")
        if directory in (pool, parent) and PVC.fullmatch(leaf):
            roots.append(name)
        elif directory == parent:
            raise ValueError(f"Unrecognized dataset under dedicated OpenEBS parent: {name}")
    roots.sort()

    def owner(name):
        return next((root for root in roots if name == root or
                     name.startswith(root + "/") or name.startswith(root + "@")), None)

    selected = {name: row for name, row in rows.items() if owner(name)}
    dependencies = {root: set() for root in roots}
    for name, row in rows.items():
        origin_root = owner(row["origin"])
        clone_root = owner(name)
        if origin_root and not clone_root:
            raise ValueError(f"External clone {name} depends on selected volume {origin_root}")
        if origin_root and clone_root != origin_root:
            dependencies[origin_root].add(clone_root)
    ordered = []
    pending = set(roots)
    while pending:
        ready = sorted(root for root in pending if not dependencies[root] & pending)
        if not ready:
            raise ValueError("Cross-volume clone dependency cycle; manual review required")
        ordered.extend(ready)
        pending.difference_update(ready)
    return {"pool": pool, "parent": parent, "roots": ordered, "objects": selected}


def hostpath_inventory(base="/var/openebs/local"):
    path = Path(base)
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink():
            raise ValueError(f"Hostpath ancestor is a symlink: {ancestor}")
    if not path.exists():
        return {}
    mounts = []
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        # Linux encodes spaces and other special bytes in mountinfo fields.
        mount = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), line.split()[4])
        mounts.append(mount)
    result = {}
    for child in sorted(path.iterdir()):
        if child.name == ".openebs-quota.lock" and not child.is_symlink() and child.is_file():
            continue
        if child.is_symlink() or not child.is_dir() or not PVC.fullmatch(child.name):
            raise ValueError(f"Unrecognized OpenEBS hostpath entry: {child}")
        if any(m == str(child) or m.startswith(str(child) + "/") for m in mounts):
            raise ValueError(f"Mounted OpenEBS hostpath volume: {child}")
        stat = child.stat()
        for directory, dirs, files in os.walk(child, followlinks=False):
            for name in dirs + files:
                entry = Path(directory) / name
                if entry.is_symlink() or entry.stat().st_dev != stat.st_dev:
                    raise ValueError(f"Hostpath contains symlink or crosses devices: {entry}")
        result[str(child)] = {"device": stat.st_dev, "inode": stat.st_ino}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool", required=True)
    parser.add_argument("--parent", required=True)
    parser.add_argument("--apply-plan", help="JSON plan produced by the inventory invocation")
    args = parser.parse_args()
    plan = inventory(args.pool, args.parent)
    plan["hostpaths"] = hostpath_inventory()
    if not args.apply_plan:
        print(json.dumps(plan, sort_keys=True))
        return
    stopped()
    with open(args.apply_plan, encoding="utf-8") as handle:
        expected = json.load(handle)
    if plan != expected:
        raise ValueError("Storage inventory changed after confirmation; inspect and confirm a new plan")
    for name, row in plan["objects"].items():
        if row["mounted"] == "yes":
            raise ValueError(f"Dataset remains mounted; uninstall/unmount before cleaning: {name}")
    for root in plan["roots"]:
        stopped()
        # Never use -R: external dependents must survive, even if they appear after inventory.
        command("zfs", "destroy", "-r", root)
        print(f"Removed OpenEBS volume {root}", flush=True)
    if not shutil.rmtree.avoids_symlink_attacks:
        raise ValueError("Platform lacks safe directory removal")
    for path, identity in plan["hostpaths"].items():
        stopped()
        if hostpath_inventory().get(path) != identity:
            raise ValueError("Hostpath identity changed after confirmation")
        shutil.rmtree(path)
        print(f"Removed OpenEBS hostpath volume {path}", flush=True)
    if hostpath_inventory():
        raise ValueError("Hostpath volumes appeared during cleanup")
    remaining = inventory(args.pool, args.parent)
    if remaining["objects"]:
        raise ValueError("OpenEBS volumes appeared during cleanup; refusing bootstrap")
    print("OpenEBS volume storage is empty; pool and parent datasets preserved")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"Storage cleanup refused: {error}", file=sys.stderr)
        sys.exit(1)
