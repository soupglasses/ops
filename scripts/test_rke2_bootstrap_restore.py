#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
#
# SPDX-License-Identifier: 0BSD

"""Restore synthetic backups through real restic, with an explicit fake DB validator.

Set RKE2_TEST_RESTIC to a restic binary and provide Docker to run integration cases.
Docker creates normalized /work/source snapshots without touching that host path.
These tests exercise selection, authenticated recovery, cleanup, and safety gates;
the production restore drill separately validates actual etcd database contents.
"""

import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('bootstrap_restore',
    pathlib.Path(__file__).with_name('rke2-bootstrap-restore.py'))
TOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOL)
IMAGE = 'quay.io/backube/volsync@sha256:0d03a6aad57569eba2c0eaa0848cf4a908d9744b372ff2224bb291a320f36d76'


class VersionSafety(unittest.TestCase):
    def test_patch_and_next_minor_are_accepted(self):
        for target in ('v1.35.8+rke2r1', 'v1.35.9+rke2r1', 'v1.36.4+rke2r1'):
            self.assertEqual(TOOL.compatible_version('rke2 version v1.35.8+rke2r1 (fixture)', target),
                             'v1.35.8+rke2r1')

    def test_downgrade_and_skipped_minor_are_refused(self):
        for target in ('v1.35.7+rke2r1', 'v1.34.9+rke2r1', 'v1.37.0+rke2r1', 'v2.0.0+rke2r1'):
            with self.assertRaises(TOOL.RestoreError):
                TOOL.compatible_version('v1.35.8+rke2r1', target)

    def test_existing_live_etcd_refuses_before_any_command_or_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            live = root / 'etcd'
            live.mkdir()
            with patch.object(TOOL, 'run', side_effect=AssertionError('Command must not run')):
                with self.assertRaisesRegex(TOOL.RestoreError, 'Existing etcd'):
                    TOOL.restore('latest', root / 'new/target', '/missing/validator',
                                 'v1.36.4+rke2r1', etcd_dir=live)
            self.assertFalse((root / 'new').exists())


@unittest.skipUnless(os.environ.get('RKE2_TEST_RESTIC'), 'Set RKE2_TEST_RESTIC for real-restic integration')
class RealResticRecovery(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='rke2-bootstrap-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.repo = self.root / 'repo'
        self.restic = os.environ['RKE2_TEST_RESTIC']
        self.environment = patch.dict(os.environ, {
            'RESTIC_REPOSITORY': str(self.repo), 'RESTIC_PASSWORD': 'synthetic-test-password',
            'RESTIC_CACHE_DIR': str(self.root / 'cache')})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        subprocess.run([self.restic, 'init'], check=True, capture_output=True)
        self.validator = self.root / 'fake-etcdutl'
        self.validator.write_text('#!/usr/bin/env python3\nimport json,sys\n'
            'if sys.argv[1:] == ["version"]: print("etcdutl version: 3.6.14")\n'
            'else: print(json.dumps({"revision": 42, "totalKey": 3, "hash": 7, "totalSize": 20}))\n')
        self.validator.chmod(0o700)
        self.target = self.root / 'restore/bootstrap'
        guid_query = patch.object(TOOL, 'dataset_guid', return_value='12345')
        guid_query.start()
        self.addCleanup(guid_query.stop)

    def create_snapshot(self, data=b'synthetic native snapshot', bad_hash=False, tag='rke2-etcd', inventory=True):
        (self.source / 'snapshot.db').write_bytes(data)
        (self.source / 'recovery.json').write_text(json.dumps({
            'sha256': '0' * 64 if bad_hash else hashlib.sha256(data).hexdigest(),
            'size': len(data), 'rke2_version': 'rke2 version v1.35.8+rke2r1 (fixture)',
            'etcd_version': '3.6.14',
            'volume_inventory': [{'namespace': 'default', 'pvc': 'database',
                                  'dataset': 'pool/k8s/pvc-fixture', 'guid': '12345'}] if inventory else []}))
        subprocess.run(['docker', 'run', '--rm', '--user', f'{os.getuid()}:{os.getgid()}',
            '--entrypoint', '/usr/local/bin/restic',
            '--mount', f'type=bind,src={self.source},dst=/work/source,readonly',
            '--mount', f'type=bind,src={self.repo},dst={self.repo}',
            '-e', 'RESTIC_REPOSITORY', '-e', 'RESTIC_PASSWORD', IMAGE,
            '--no-cache', 'backup', '--host', 'nas', '--tag', tag, '/work/source'],
            check=True, capture_output=True)
        snapshots = json.loads(subprocess.check_output([self.restic, 'snapshots', '--json', '--latest', '1']))
        return snapshots[-1]['id']

    def restore(self, snapshot='latest', target_version='v1.36.4+rke2r1'):
        return TOOL.restore(snapshot, self.target, str(self.validator), target_version,
                            etcd_dir=self.root / 'absent-etcd', restic=self.restic)

    def assert_clean_failure(self):
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.target.parent.glob('.rke2-bootstrap-*')), [])

    def test_latest_restore_publishes_only_verified_database(self):
        selected = self.create_snapshot()
        self.create_snapshot(b'unrelated history import', tag='rke2-etcd-history')
        receipt = self.restore()
        self.assertEqual(receipt['restic_snapshot'], selected)
        self.assertEqual((self.target / 'snapshot.db').read_bytes(), b'synthetic native snapshot')
        self.assertEqual(sorted(p.name for p in self.target.iterdir()), ['snapshot.db'])
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.target / 'snapshot.db').stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.target.parent.glob('.rke2-bootstrap-*')), [])

    def test_exact_restore_selects_older_recovery_point(self):
        selected = self.create_snapshot(b'older snapshot')
        self.create_snapshot(b'newer snapshot')
        receipt = self.restore(selected)
        self.assertEqual(receipt['restic_snapshot'], selected)
        self.assertEqual((self.target / 'snapshot.db').read_bytes(), b'older snapshot')

    def test_checksum_mismatch_removes_plaintext_staging(self):
        self.create_snapshot(bad_hash=True)
        with self.assertRaisesRegex(TOOL.RestoreError, 'does not match'):
            self.restore()
        self.assert_clean_failure()

    def test_wrong_password_does_not_create_empty_bootstrap(self):
        self.create_snapshot()
        with patch.dict(os.environ, {'RESTIC_PASSWORD': 'wrong-password'}):
            with self.assertRaises(TOOL.RestoreError):
                self.restore()
        self.assert_clean_failure()

    def test_incompatible_version_removes_plaintext_staging(self):
        self.create_snapshot()
        with self.assertRaisesRegex(TOOL.RestoreError, 'skip a minor'):
            self.restore(target_version='v1.37.0+rke2r1')
        self.assert_clean_failure()

    def test_native_validation_failure_removes_plaintext_staging(self):
        self.create_snapshot()
        self.validator.write_text(self.validator.read_text().replace('"totalKey": 3', '"totalKey": 0'))
        with self.assertRaisesRegex(TOOL.RestoreError, 'no usable state'):
            self.restore()
        self.assert_clean_failure()

    def test_missing_volume_inventory_refuses_etcd_restore(self):
        self.create_snapshot(inventory=False)
        with self.assertRaisesRegex(TOOL.RestoreError, 'no volume inventory'):
            self.restore()
        self.assert_clean_failure()

    def test_replaced_dataset_refuses_etcd_restore(self):
        self.create_snapshot()
        with patch.object(TOOL, 'dataset_guid', return_value='67890'):
            with self.assertRaisesRegex(TOOL.RestoreError, 'Dataset identity changed'):
                self.restore()
        self.assert_clean_failure()

    def test_missing_requested_snapshot_has_no_fallback(self):
        self.create_snapshot()
        with self.assertRaisesRegex(TOOL.RestoreError, 'missing, ambiguous'):
            self.restore('0' * 64)
        self.assert_clean_failure()


if __name__ == '__main__':
    unittest.main()
