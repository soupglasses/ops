# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
#
# SPDX-License-Identifier: MIT

import fcntl
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location(
    "media_restore", Path(__file__).parents[1] / "media_restore.py"
)
media = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(media)
OLD = "a" * 64
NEW = "b" * 64


class MediaRestoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "media"
        self.root.mkdir()
        self.fixture = Path(self.temporary.name) / "backup"
        self.fixture.mkdir()
        media.reconcile(self.fixture, allow_initialize=True, run=lambda _: b"[]")
        (self.fixture / "movie.mkv").write_bytes(b"representative media file")

    def test_empty_repository_requires_explicit_initialization_and_creates_verifiable_canary(
        self,
    ):
        for library in media.LIBRARIES:
            (self.root / library).mkdir()
        with self.assertRaisesRegex(RuntimeError, "explicit first initialization"):
            media.reconcile(self.root, run=lambda _: b"[]")
        result = media.reconcile(self.root, allow_initialize=True, run=lambda _: b"[]")
        self.assertTrue(result["initialized"])
        media.validate(self.root)
        self.assertFalse((self.root / media.PROGRESS).exists())

    def test_nonempty_library_folder_does_not_count_as_an_empty_dataset(self):
        (self.root / "movies").mkdir()
        (self.root / "movies/important.mkv").write_bytes(b"preserve")
        with self.assertRaises(FileNotFoundError):
            media.reconcile(self.root, allow_initialize=True, run=lambda _: b"[]")
        self.assertEqual((self.root / "movies/important.mkv").read_bytes(), b"preserve")

    def test_repository_failure_never_initializes_even_with_permission(self):
        def failing(_):
            raise RuntimeError("repository inaccessible")

        with self.assertRaisesRegex(RuntimeError, "inaccessible"):
            media.reconcile(self.root, allow_initialize=True, run=failing)
        self.assertFalse((self.root / media.CANARY).exists())
        self.assertFalse((self.root / media.MANIFEST).exists())

    def test_explicit_repository_initialization_still_requires_successful_repository_read(
        self,
    ):
        calls = []

        def unavailable(arguments):
            calls.append(arguments[0])
            raise RuntimeError("access denied")

        with self.assertRaisesRegex(RuntimeError, "access denied"):
            media.reconcile(
                self.root,
                allow_initialize=True,
                initialize_repository=True,
                run=unavailable,
            )
        self.assertEqual(calls, ["snapshots", "init", "snapshots"])
        self.assertFalse((self.root / media.CANARY).exists())

    def test_explicit_repository_initialization_accepts_only_verified_empty_repository(
        self,
    ):
        calls = []

        def missing_then_initialized(arguments):
            calls.append(arguments[0])
            if calls == ["snapshots"]:
                raise RuntimeError("missing repository")
            return b"[]" if arguments[0] == "snapshots" else b""

        result = media.reconcile(
            self.root,
            allow_initialize=True,
            initialize_repository=True,
            run=missing_then_initialized,
        )
        self.assertTrue(result["initialized"])
        self.assertEqual(calls, ["snapshots", "init", "snapshots"])

    def test_preserves_existing_verified_dataset_without_replaying_older_snapshot(self):
        self.populate(self.root)
        (self.root / "movie.mkv").write_bytes(b"newer user data")

        def unexpected(_):
            raise AssertionError("surviving dataset must not be overwritten")

        self.assertTrue(media.reconcile(self.root, run=unexpected)["preserved"])
        self.assertEqual((self.root / "movie.mkv").read_bytes(), b"newer user data")

    def test_unknown_existing_files_are_preserved_and_require_manual_validation(self):
        (self.root / "important.mkv").write_bytes(b"preserve")
        with self.assertRaises(FileNotFoundError):
            media.reconcile(self.root, allow_initialize=True, run=lambda _: b"[]")
        self.assertEqual((self.root / "important.mkv").read_bytes(), b"preserve")
        self.assertFalse((self.root / media.PROGRESS).exists())

    def test_clean_rebuild_restores_fixed_snapshot_and_checks_canary(self):
        calls = []

        def run(arguments):
            calls.append(arguments)
            if arguments[0] == "snapshots":
                return self.snapshots()
            self.populate(self.root)
            return b""

        self.assertEqual(media.reconcile(self.root, run=run), {"restored": OLD})
        self.assertIn("--verify", calls[1])
        self.assertEqual(calls[1][-2:], ["--exclude", ".ops-media-restore*"])
        self.assertEqual(
            (self.root / "movie.mkv").read_bytes(), b"representative media file"
        )
        self.assertFalse((self.root / media.PROGRESS).exists())

    def test_interrupted_recovery_retries_original_snapshot_when_newer_backup_appears(
        self,
    ):
        def failing(arguments):
            if arguments[0] == "snapshots":
                return self.snapshots()
            (self.root / "movie.mkv").write_bytes(b"partial")
            raise RuntimeError("interrupted restore")

        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            media.reconcile(self.root, run=failing)
        self.assertEqual(
            json.loads((self.root / media.PROGRESS).read_text())["snapshot"], OLD
        )

        def retry(arguments):
            if arguments[0] == "snapshots":
                return self.snapshots(newer=True)
            self.assertEqual(arguments[1], OLD)
            self.populate(self.root)
            return b""

        self.assertEqual(media.reconcile(self.root, run=retry), {"restored": OLD})

    def test_invalid_canary_does_not_mark_recovery_complete(self):
        def run(arguments):
            if arguments[0] == "snapshots":
                return self.snapshots()
            self.populate(self.root)
            (self.root / media.CANARY).write_bytes(b"wrong")
            return b""

        with self.assertRaisesRegex(RuntimeError, "canary"):
            media.reconcile(self.root, run=run)
        self.assertTrue((self.root / media.PROGRESS).exists())

    def test_exclusive_recovery_lock_prevents_overlapping_restores(self):
        with (self.root / media.LOCK).open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                media.reconcile(self.root, allow_initialize=True, run=lambda _: b"[]")

    def populate(self, root):
        for path in self.fixture.iterdir():
            if path.name != media.LOCK:
                shutil.copy2(path, root / path.name)

    @staticmethod
    def snapshots(newer=False):
        items = [{"id": OLD, "time": "2026-10-17T01:00:00Z"}]
        if newer:
            items.append({"id": NEW, "time": "2026-10-18T01:00:00Z"})
        return json.dumps(items).encode()


if __name__ == "__main__":
    unittest.main()
