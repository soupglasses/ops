# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
#
# SPDX-License-Identifier: MIT

import importlib.util
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from unittest import mock

RUNTIME = Path(__file__).parents[1] / "runtime.py"
SPEC = importlib.util.spec_from_file_location("runtime", RUNTIME)
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "config/server"
        self.source.mkdir(parents=True)
        self.staging = self.root / "backup"
        (self.source / "config").mkdir()
        (self.source / "config/system.xml").write_text("<ServerConfiguration />")
        (self.source / "plugins/configurations").mkdir(parents=True)
        (self.source / "plugins/configurations/ldap.xml").write_text("<LDAP />")
        (self.source / "metadata/users").mkdir(parents=True)
        (self.source / "metadata/users/photo.jpg").write_bytes(b"\xff\xd8photo\xff\xd9")
        connection = sqlite3.connect(self.source / "jellyfin.db")
        connection.executescript(
            "CREATE TABLE users(name TEXT); INSERT INTO users VALUES ('Alice');"
        )
        connection.close()

    def test_backup_restores_database_plugins_and_photo_without_overwriting_live_state(
        self,
    ):
        prepared = runtime.checkpoint(self.source, self.staging)
        destination = self.root / "restored"
        destination.mkdir()
        shutil.copy2(self.staging / "checkpoint.tar", destination)
        # A snapshot can include an interrupted next export; only the published archive matters.
        (destination / "checkpoint.tar.partial").write_bytes(b"incomplete")
        open_archive = runtime.tarfile.open

        def require_restore_headroom(*args, **kwargs):
            self.assertFalse((destination / "checkpoint.tar.partial").exists())
            self.assertTrue((destination / "checkpoint.tar").exists())
            return open_archive(*args, **kwargs)

        with mock.patch.object(runtime.tarfile, "open", side_effect=require_restore_headroom):
            runtime.restore(destination)
        self.assertEqual(
            runtime.inventory(self.source), runtime.inventory(destination / "server")
        )
        with sqlite3.connect(destination / "server/jellyfin.db") as connection:
            self.assertEqual(
                connection.execute("SELECT name FROM users").fetchall(), [("Alice",)]
            )
        self.assertEqual(prepared["databases"], ["jellyfin.db"])
        (destination / "server/config/system.xml").write_text("<Changed />")
        shutil.copy2(self.staging / "checkpoint.tar", destination)
        runtime.restore(destination)
        self.assertEqual(
            (destination / "server/config/system.xml").read_text(), "<Changed />"
        )

    def test_missing_or_corrupt_database_does_not_replace_previous_checkpoint(self):
        runtime.checkpoint(self.source, self.staging)
        previous = (self.staging / "checkpoint.tar").read_bytes()
        (self.source / "jellyfin.db").write_bytes(b"corrupt")
        with self.assertRaisesRegex(RuntimeError, "invalid SQLite"):
            runtime.checkpoint(self.source, self.staging)
        self.assertEqual((self.staging / "checkpoint.tar").read_bytes(), previous)

    def test_failed_archive_creation_preserves_previous_checkpoint(self):
        runtime.checkpoint(self.source, self.staging)
        previous = (self.staging / "checkpoint.tar").read_bytes()
        with mock.patch.object(
            tarfile.TarFile, "add", side_effect=OSError("disk full")
        ):
            with self.assertRaisesRegex(OSError, "disk full"):
                runtime.checkpoint(self.source, self.staging)
        self.assertEqual((self.staging / "checkpoint.tar").read_bytes(), previous)
        self.assertFalse((self.staging / "checkpoint.tar.partial").exists())

    def test_restoration_fails_closed_for_tampered_content(self):
        runtime.checkpoint(self.source, self.staging)
        destination = self.root / "bad-restore"
        destination.mkdir()
        with tarfile.open(self.staging / "checkpoint.tar") as source:
            with tarfile.open(destination / "checkpoint.tar", "w") as output:
                for member in source.getmembers():
                    content = source.extractfile(member).read()
                    if member.name.endswith("photo.jpg"):
                        content = b"changed"
                    member.size = len(content)
                    output.addfile(member, io.BytesIO(content))
        with self.assertRaisesRegex(RuntimeError, "inventory"):
            runtime.restore(destination)
        self.assertFalse((destination / "server").exists())
        self.assertTrue((destination / "checkpoint.tar").exists())

    def test_unsafe_archive_member_is_rejected(self):
        runtime.checkpoint(self.source, self.staging)
        destination = self.root / "unsafe-restore"
        destination.mkdir()
        with tarfile.open(self.staging / "checkpoint.tar") as source:
            with tarfile.open(destination / "checkpoint.tar", "w") as output:
                for member in source.getmembers():
                    content = source.extractfile(member).read()
                    if member.name.endswith("photo.jpg"):
                        member.name = "server/../../outside"
                    output.addfile(member, io.BytesIO(content))
        with self.assertRaisesRegex(RuntimeError, "unsafe"):
            runtime.restore(destination)
        self.assertFalse((self.root / "outside").exists())

    def test_only_explicit_first_initialization_permits_empty_directory(self):
        destination = self.root / "new"
        destination.mkdir()
        with self.assertRaisesRegex(RuntimeError, "missing initialized"):
            runtime.restore(destination)
        runtime.restore(destination, allow_initialize=True)
        self.assertTrue((destination / "server").is_dir())
        with self.assertRaisesRegex(RuntimeError, "no Jellyfin database"):
            runtime.restore(destination)

    def test_supervisor_stops_child_before_copy_and_resumes_after_success_and_failure(
        self,
    ):
        child = self.root / "child.py"
        child.write_text("""import os, signal, sqlite3, sys, time
from pathlib import Path
root = Path(sys.argv[1])
db = sqlite3.connect(root / "jellyfin.db")
def stop(*_):
    (root / "child-stopping").touch()
    time.sleep(0.5)
    db.execute("INSERT INTO users VALUES ('graceful-stop')")
    db.commit()
    db.close()
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
(root / "child-ready").write_text(str(os.getpid()))
while True: time.sleep(0.05)
""")
        control = self.root / "control.sock"
        process = subprocess.Popen(
            [
                sys.executable,
                str(RUNTIME),
                "serve",
                "--source",
                str(self.source),
                "--staging",
                str(self.staging),
                "--socket",
                str(control),
                "--",
                sys.executable,
                str(child),
                str(self.source),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        self.addCleanup(self.stop_process, process)
        self.wait_for(
            lambda: control.exists() and (self.source / "child-ready").exists()
        )
        previous_pid = (self.source / "child-ready").read_text()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.request, control, "checkpoint")
            self.wait_for(lambda: (self.source / "child-stopping").exists())
            started = time.monotonic()
            self.assertTrue(self.request(control, "health")["ok"])
            self.assertLess(time.monotonic() - started, 0.25)
            result = future.result()
        self.assertTrue(result["ok"], result)
        destination = self.root / "stopped-copy"
        destination.mkdir()
        shutil.copy2(self.staging / "checkpoint.tar", destination)
        runtime.restore(destination)
        with sqlite3.connect(destination / "server/jellyfin.db") as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM users WHERE name='graceful-stop'"
                ).fetchone(),
                (1,),
            )
        self.assertTrue(self.request(control, "health")["ok"])
        self.wait_for(lambda: (self.source / "child-ready").read_text() != previous_pid)
        (self.source / "config/system.xml").unlink()
        result = self.request(control, "checkpoint")
        self.assertFalse(result["ok"])
        self.assertTrue(self.request(control, "health")["ok"])

    def test_shutdown_timeout_withholds_checkpoint_and_preserves_previous_archive(self):
        runtime.checkpoint(self.source, self.staging)
        previous = (self.staging / "checkpoint.tar").read_bytes()
        child = self.root / "stubborn.py"
        ready = self.root / "stubborn-ready"
        child.write_text("""import signal, sys, time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path(sys.argv[1]).touch()
while True: time.sleep(0.05)
""")
        supervisor = runtime.Supervisor(
            [sys.executable, str(child), str(ready)], self.source, self.staging, 0.05
        )
        self.addCleanup(lambda: supervisor.child.wait())
        self.addCleanup(lambda: supervisor.child.kill())
        self.wait_for(ready.exists)
        with self.assertRaises(subprocess.TimeoutExpired):
            supervisor.prepare()
        self.assertIsNone(supervisor.child.poll())
        self.assertEqual((self.staging / "checkpoint.tar").read_bytes(), previous)

    def test_unclean_child_exit_withholds_checkpoint(self):
        runtime.checkpoint(self.source, self.staging)
        previous = (self.staging / "checkpoint.tar").read_bytes()
        child = self.root / "unclean.py"
        ready = self.root / "unclean-ready"
        child.write_text("""import signal, sys, time
from pathlib import Path
signal.signal(signal.SIGTERM, lambda *_: sys.exit(2))
Path(sys.argv[1]).touch()
while True: time.sleep(0.05)
""")
        supervisor = runtime.Supervisor(
            [sys.executable, str(child), str(ready)], self.source, self.staging, 1
        )
        self.addCleanup(lambda: supervisor.child.wait())
        self.addCleanup(lambda: supervisor.child.kill())
        self.wait_for(ready.exists)
        with self.assertRaisesRegex(RuntimeError, "did not exit cleanly"):
            supervisor.prepare()
        self.assertIsNone(supervisor.child.poll())
        self.assertEqual((self.staging / "checkpoint.tar").read_bytes(), previous)

    def test_image_owned_plugins_replace_derived_binaries_and_preserve_configuration(
        self,
    ):
        distribution = self.root / "image-plugins"
        (distribution / "LDAP-Auth_23").mkdir(parents=True)
        (distribution / "LDAP-Auth_23/LDAP.dll").write_bytes(b"pinned plugin")
        (self.source / "plugins/LDAP-Auth_22").mkdir()
        (self.source / "plugins/LDAP-Auth_22/LDAP.dll").write_bytes(b"old plugin")
        (self.source / "plugins/Other_1").mkdir()
        runtime.seed_plugins(self.source, distribution)
        self.assertFalse((self.source / "plugins/LDAP-Auth_22").exists())
        self.assertEqual(
            (self.source / "plugins/LDAP-Auth_23/LDAP.dll").read_bytes(),
            b"pinned plugin",
        )
        self.assertEqual(
            (self.source / "plugins/configurations/ldap.xml").read_text(), "<LDAP />"
        )
        self.assertTrue((self.source / "plugins/Other_1").is_dir())
        fresh = self.root / "fresh/config/server"
        runtime.seed_plugins(fresh, distribution)
        self.assertEqual(
            (fresh / "plugins/LDAP-Auth_23/LDAP.dll").read_bytes(), b"pinned plugin"
        )

    @staticmethod
    def request(path, command):
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(10)
            client.connect(str(path))
            client.sendall(command.encode() + b"\n")
            return json.loads(client.makefile("rb").readline())

    @staticmethod
    def wait_for(predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.05)
        raise AssertionError("child or supervisor did not become ready")

    @staticmethod
    def stop_process(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        process.stderr.close()


if __name__ == "__main__":
    unittest.main()
