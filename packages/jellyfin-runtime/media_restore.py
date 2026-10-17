# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
#
# SPDX-License-Identifier: MIT

"""Restore an empty media dataset without replacing surviving NAS data."""

import argparse
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

LOCK = ".ops-media-restore.lock"
PROGRESS = ".ops-media-restore.json"
CANARY = ".ops-media-canary"
MANIFEST = ".ops-media-manifest.json"
LIBRARIES = {"movies", "tv", "music"}


def content_entries(root):
    return {
        path.name
        for path in root.iterdir()
        if path.name != LOCK
        and not (
            path.name in LIBRARIES
            and path.is_dir()
            and not path.is_symlink()
            and not any(path.iterdir())
        )
    }


def durable_json(path, value):
    temporary = path.with_name(path.name + ".partial")
    with temporary.open("w") as output:
        json.dump(value, output, sort_keys=True)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def validate(root):
    if (root / MANIFEST).is_symlink() or (root / CANARY).is_symlink():
        raise RuntimeError("media recovery metadata cannot be symbolic links")
    manifest = json.loads((root / MANIFEST).read_text())
    if manifest["version"] != 1 or manifest["canary"] != CANARY:
        raise RuntimeError("unsupported media recovery manifest")
    if hashlib.sha256((root / CANARY).read_bytes()).hexdigest() != manifest["sha256"]:
        raise RuntimeError("media recovery canary does not match its manifest")
    return manifest


def restic(arguments):
    result = subprocess.run(["restic", "--no-cache", *arguments], capture_output=True)
    if result.returncode:
        # Repository URLs and provider messages can contain credentials.
        raise RuntimeError(
            "restic operation failed; media was not initialized or accepted"
        )
    return result.stdout


def initialize(root, progress):
    allowed = {
        LOCK,
        PROGRESS,
        PROGRESS + ".partial",
        CANARY,
        MANIFEST,
        MANIFEST + ".partial",
    }
    if content_entries(root) - allowed:
        raise RuntimeError(
            "media appeared during initialization; preserve and inspect it"
        )
    canary = bytes.fromhex(progress["canary"])
    path = root / CANARY
    if path.exists() and path.read_bytes() != canary:
        raise RuntimeError("refusing to replace an existing media canary")
    with path.open("wb") as output:
        output.write(canary)
        output.flush()
        os.fsync(output.fileno())
    durable_json(
        root / MANIFEST,
        {
            "version": 1,
            "created": progress["created"],
            "canary": CANARY,
            "sha256": hashlib.sha256(canary).hexdigest(),
        },
    )
    validate(root)
    (root / PROGRESS).unlink()
    return {"initialized": True}


def reconcile(
    root,
    snapshot="latest",
    allow_initialize=False,
    run=restic,
    initialize_repository=False,
):
    if not root.is_dir() or root.is_symlink():
        raise RuntimeError("media root must be the mounted dataset directory")
    if (root / LOCK).is_symlink() or (root / PROGRESS).is_symlink():
        raise RuntimeError("media recovery control files cannot be symbolic links")
    with (root / LOCK).open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        progress_path = root / PROGRESS
        progress = (
            json.loads(progress_path.read_text()) if progress_path.exists() else None
        )
        entries = content_entries(root)
        if progress is None and entries:
            validate(root)
            return {"preserved": True}

        try:
            snapshots_raw = run(["snapshots", "--json"])
        except RuntimeError:
            if not (
                allow_initialize
                and initialize_repository
                and (
                    not entries
                    or (progress is not None and progress["phase"] == "initialize")
                )
            ):
                raise
            try:
                run(["init"])
            except RuntimeError:
                # A concurrent initializer can win; only a subsequent successful
                # repository read permits us to continue.
                pass
            snapshots_raw = run(["snapshots", "--json"])
        snapshots = json.loads(snapshots_raw)
        if not isinstance(snapshots, list):
            raise RuntimeError("unexpected media repository snapshot response")
        if progress is not None and progress["phase"] == "initialize":
            if snapshots:
                raise RuntimeError(
                    "repository gained history during initialization; inspect manually"
                )
            if not allow_initialize:
                raise RuntimeError(
                    "unfinished first initialization still requires explicit permission"
                )
            return initialize(root, progress)
        if not snapshots:
            if progress is not None:
                raise RuntimeError(
                    "the selected recovery snapshot is no longer available"
                )
            if not allow_initialize:
                raise RuntimeError(
                    "no media backups exist; explicit first initialization required"
                )
            progress = {
                "phase": "initialize",
                "created": time.time(),
                "canary": os.urandom(64).hex(),
            }
            durable_json(progress_path, progress)
            return initialize(root, progress)

        if progress is None:
            if snapshot == "latest":
                selected = max(
                    snapshots, key=lambda item: datetime.fromisoformat(item["time"])
                )["id"]
            else:
                matches = [
                    item["id"] for item in snapshots if item["id"].startswith(snapshot)
                ]
                if len(matches) != 1:
                    raise RuntimeError(
                        "media recovery snapshot is missing or ambiguous"
                    )
                selected = matches[0]
            if re.fullmatch("[0-9a-f]{64}", selected) is None:
                raise RuntimeError("unexpected media recovery snapshot identifier")
            progress = {"phase": "restore", "snapshot": selected}
            durable_json(progress_path, progress)
        if progress["phase"] != "restore" or progress["snapshot"] not in {
            item["id"] for item in snapshots
        }:
            raise RuntimeError("recorded media recovery snapshot is unavailable")
        # Repeated attempts use the original snapshot, even if a newer backup appears.
        # External writers must remain stopped until recovery finishes.
        run(
            [
                "restore",
                progress["snapshot"],
                "--target",
                str(root),
                "--verify",
                "--exclude",
                ".ops-media-restore*",
            ]
        )
        validate(root)
        progress_path.unlink()
        return {"restored": progress["snapshot"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/media"))
    parser.add_argument("--snapshot", default="latest")
    parser.add_argument("--allow-initialize", action="store_true")
    parser.add_argument("--initialize-repository", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            reconcile(
                args.root,
                args.snapshot,
                args.allow_initialize,
                initialize_repository=args.initialize_repository,
            )
        )
    )


if __name__ == "__main__":
    main()
