# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
#
# SPDX-License-Identifier: MIT

"""Supervise Jellyfin and prepare complete, offline filesystem checkpoints."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import socketserver
import sqlite3
import tarfile
import io
import subprocess
import threading
import time
import uuid


def inventory(tree):
    result = {}
    for path in sorted(tree.rglob("*")):
        relative = str(path.relative_to(tree))
        if path.is_symlink():
            raise RuntimeError(f"checkpoint cannot include symlinks: {relative}")
        if path.is_file():
            with path.open("rb") as stream:
                result[relative] = hashlib.file_digest(stream, "sha256").hexdigest()
    return result


def verify(tree, expected=None):
    actual = inventory(tree)
    if expected is not None and actual != expected:
        raise RuntimeError("checkpoint file inventory does not match its manifest")
    databases = []
    for path in tree.rglob("*.db"):
        with path.open("rb") as stream:
            if stream.read(16) != b"SQLite format 3\x00":
                raise RuntimeError(f"invalid SQLite database: {path.name}")
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise RuntimeError(f"SQLite integrity check failed: {path.name}")
        finally:
            connection.close()
        databases.append(str(path.relative_to(tree)))
    if not databases:
        raise RuntimeError("no Jellyfin database found; refusing an empty checkpoint")
    if not (tree / "config/system.xml").is_file():
        raise RuntimeError("Jellyfin system.xml is missing")
    return inventory(tree), databases


def checkpoint(source, staging):
    staging.mkdir(parents=True, exist_ok=True)
    temporary = staging / "checkpoint.tar.partial"
    files, databases = verify(source)
    manifest = {
        "version": 1,
        "generation": uuid.uuid4().hex,
        "created": time.time(),
        "files": files,
        "databases": databases,
    }
    try:
        with tarfile.open(temporary, "w") as archive:
            for relative in files:
                archive.add(
                    source / relative, arcname="server/" + relative, recursive=False
                )
            content = json.dumps(manifest, sort_keys=True).encode()
            entry = tarfile.TarInfo("manifest.json")
            entry.size = len(content)
            archive.addfile(entry, io.BytesIO(content))
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        # The old archive remains usable until this single atomic publication.
        os.replace(temporary, staging / "checkpoint.tar")
        directory = os.open(staging, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return {
            "generation": manifest["generation"],
            "databases": databases,
            "files": len(files),
        }
    finally:
        temporary.unlink(missing_ok=True)


def restore(root, allow_initialize=False):
    target = root / "server"
    if target.is_symlink():
        raise RuntimeError("native Jellyfin server directory cannot be a symlink")
    if target.exists():
        if allow_initialize and not any(target.iterdir()):
            return
        verify(target)
        return
    backup = root / "checkpoint.tar"
    if not backup.is_file():
        if allow_initialize and not any(root.iterdir()):
            target.mkdir()
            print(
                "Explicitly allowing first initialization of an empty directory",
                flush=True,
            )
            return
        raise RuntimeError("missing initialized Jellyfin server and checkpoint archive")
    # Unpublished exports are disposable and must not consume extraction headroom.
    (root / "checkpoint.tar.partial").unlink(missing_ok=True)
    temporary = root / (".restore-" + uuid.uuid4().hex)
    for previous in root.glob(".restore-*"):
        if previous.is_dir() and not previous.is_symlink():
            shutil.rmtree(previous)
    temporary.mkdir()
    try:
        with tarfile.open(backup, "r") as archive:
            manifest = json.load(archive.extractfile("manifest.json"))
            if manifest["version"] != 1:
                raise RuntimeError("unsupported checkpoint format")
            expected = {"server/" + name for name in manifest["files"]}
            members = archive.getmembers()
            if len(members) != len(expected) + 1:
                raise RuntimeError("unexpected or duplicate checkpoint archive entries")
            for member in members:
                path = Path(member.name)
                if (
                    not member.isfile()
                    or path.is_absolute()
                    or ".." in path.parts
                    or member.name not in expected | {"manifest.json"}
                ):
                    raise RuntimeError("unsafe checkpoint archive member")
                destination = temporary / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, destination.open(
                    "wb"
                ) as output:
                    shutil.copyfileobj(source, output)
                os.chmod(destination, member.mode & 0o777)
        verify(temporary / "server", manifest["files"])
        os.sync()
        # Never replace native state. Restored archives are promoted only once.
        os.rename(temporary / "server", target)
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        backup.unlink()
        print(
            json.dumps(
                {"restored": manifest["generation"], "files": len(manifest["files"])}
            ),
            flush=True,
        )
    finally:
        shutil.rmtree(temporary)


def seed_plugins(source, distribution=Path("/opt/jellyfin/plugins")):
    if not distribution.exists():
        return
    plugins = source / "plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    for plugin in distribution.iterdir():
        prefix = next(
            (
                name
                for name in ("LDAP-Auth_", "SSO-Auth_")
                if plugin.name.startswith(name)
            ),
            None,
        )
        if prefix is None or not plugin.is_dir() or plugin.is_symlink():
            raise RuntimeError("unexpected image-managed plugin distribution")
        temporary = plugins / (".seed-" + uuid.uuid4().hex)
        shutil.copytree(plugin, temporary)
        target = plugins / plugin.name
        if target.is_symlink():
            raise RuntimeError("image-managed plugin target cannot be a symlink")
        if target.exists():
            shutil.rmtree(target)
        os.rename(temporary, target)
        for previous in plugins.glob(prefix + "*"):
            if previous != target:
                if not previous.is_dir() or previous.is_symlink():
                    raise RuntimeError("unexpected image-managed plugin path")
                shutil.rmtree(previous)


class Supervisor:
    def __init__(self, command, source, staging, stop_timeout):
        self.command = command
        self.source = source
        self.staging = staging
        self.stop_timeout = stop_timeout
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.child = subprocess.Popen(command)
        self.paused = False

    def shutdown(self, *_):
        self.stopping.set()
        if self.child.poll() is None:
            self.child.terminate()

    def prepare(self):
        if not self.lock.acquire(blocking=False):
            raise RuntimeError("another checkpoint is already running")
        try:
            if self.stopping.is_set() or self.child.poll() is not None:
                raise RuntimeError("Jellyfin is unavailable")
            self.paused = True
            self.child.terminate()
            try:
                self.child.wait(timeout=self.stop_timeout)
                if self.child.returncode != 0:
                    raise RuntimeError(
                        "Jellyfin did not exit cleanly; checkpoint withheld"
                    )
                return checkpoint(self.source, self.staging)
            finally:
                if self.child.poll() is not None and not self.stopping.is_set():
                    self.child = subprocess.Popen(self.command)
                self.paused = False
        finally:
            self.lock.release()


def serve(args):
    seed_plugins(args.source)
    supervisor = Supervisor(args.command, args.source, args.staging, args.stop_timeout)
    signal.signal(signal.SIGTERM, supervisor.shutdown)
    signal.signal(signal.SIGINT, supervisor.shutdown)

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            try:
                command = self.rfile.readline(1024)
                if command == b"health\n":
                    result = {
                        "ok": not supervisor.stopping.is_set()
                        and (supervisor.paused or supervisor.child.poll() is None)
                    }
                elif command == b"checkpoint\n":
                    result = {"ok": True, **supervisor.prepare()}
                else:
                    raise RuntimeError("unknown control command")
            except Exception as error:
                result = {"ok": False, "error": str(error)}
            self.wfile.write(json.dumps(result).encode() + b"\n")

    args.socket.parent.mkdir(parents=True, exist_ok=True)
    args.socket.unlink(missing_ok=True)
    server = socketserver.ThreadingUnixStreamServer(str(args.socket), Handler)
    server.daemon_threads = True
    os.chmod(args.socket, 0o600)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    while True:
        if supervisor.stopping.is_set():
            supervisor.child.wait()
            return
        if not supervisor.paused and supervisor.child.poll() is not None:
            raise RuntimeError("Jellyfin exited unexpectedly")
        time.sleep(0.2)


def control(args):
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(args.timeout)
        client.connect(str(args.socket))
        client.sendall(args.action.encode() + b"\n")
        result = json.loads(client.makefile("rb").readline())
    print(json.dumps(result), flush=True)
    if not result["ok"]:
        raise RuntimeError(result.get("error", "supervisor is unavailable"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("serve", "checkpoint", "health", "restore", "verify"):
        command = sub.add_parser(action)
        command.add_argument("--source", type=Path, default=Path("/config/server"))
        command.add_argument("--staging", type=Path, default=Path("/backup"))
        command.add_argument(
            "--socket", type=Path, default=Path("/run/jellyfin/control.sock")
        )
        command.add_argument("--timeout", type=int, default=600)
        command.add_argument("--stop-timeout", type=int, default=90)
        command.add_argument("--allow-initialize", action="store_true")
        if action == "restore":
            command.set_defaults(source=Path("/config"))
        if action == "serve":
            command.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.action == "serve":
        if args.command[:1] == ["--"]:
            args.command = args.command[1:]
        serve(args)
    elif args.action in ("checkpoint", "health"):
        control(args)
    elif args.action == "restore":
        restore(args.source, args.allow_initialize)
    else:
        files, databases = verify(args.source)
        print(json.dumps({"files": len(files), "databases": databases}))


if __name__ == "__main__":
    main()
