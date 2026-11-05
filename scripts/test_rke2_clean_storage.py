# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
#
# SPDX-License-Identifier: 0BSD

"""Exercise cleanup through its CLI against a stateful fake ZFS executable."""

import json
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HELPER = Path(__file__).with_name("rke2-clean-storage.py")
A = "zfspool/k8s/pvc-11111111-1111-1111-1111-111111111111"
B = "zfspool/pvc-22222222-2222-2222-2222-222222222222"
FAKE = r'''#!/usr/bin/python3
import json, os, sys
from pathlib import Path
path = Path(os.environ['FAKE_STATE'])
s = json.loads(path.read_text())
name = Path(sys.argv[0]).name
if name == 'systemctl':
    print(s.get('service', 'inactive')); sys.exit(3)
if name == 'pgrep':
    sys.exit(0 if s.get('kubelet') else 1)
if sys.argv[1] == 'list':
    for key, row in s['rows'].items():
        print('\t'.join([key] + row))
elif sys.argv[1:3] == ['destroy', '-r']:
    root = sys.argv[3]
    assert len(sys.argv) == 4
    s.setdefault('deleted', []).append(root)
    s['rows'] = {k:v for k,v in s['rows'].items()
                 if not (k == root or k.startswith(root + '/') or k.startswith(root + '@'))}
    path.write_text(json.dumps(s))
else:
    sys.exit(99)
'''


class CleanupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.state = self.directory / "state.json"
        self.plan = self.directory / "plan.json"
        for executable in ("zfs", "systemctl", "pgrep"):
            path = self.directory / executable
            path.write_text(FAKE)
            path.chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.directory), FAKE_STATE=str(self.state))
        self.write({"rows": {
            "zfspool": ["1", "filesystem", "no", "-"],
            "zfspool/k8s": ["2", "filesystem", "no", "-"],
            "zfspool/photos": ["3", "filesystem", "yes", "-"],
            A: ["4", "filesystem", "no", "-"],
            A + "@snapshot-a": ["5", "snapshot", "-", "-"],
            B: ["6", "filesystem", "no", A + "@snapshot-a"],
        }})

    def write(self, state):
        self.state.write_text(json.dumps(state))

    def run_helper(self, apply=False):
        args = [sys.executable, str(HELPER), "--pool", "zfspool", "--parent", "zfspool/k8s"]
        if apply:
            args += ["--apply-plan", str(self.plan)]
        return subprocess.run(args, env=self.env, capture_output=True, text=True)

    def save_plan(self):
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.plan.write_text(result.stdout)
        return json.loads(result.stdout)

    def test_clone_first_cleanup_preserves_unrelated_data_and_is_repeatable(self):
        plan = self.save_plan()
        self.assertEqual(plan['roots'], [B, A])
        result = self.run_helper(True)
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(self.state.read_text())
        self.assertEqual(state['deleted'], [B, A])
        self.assertEqual(set(state['rows']), {'zfspool', 'zfspool/k8s', 'zfspool/photos'})
        self.save_plan()
        self.assertEqual(self.run_helper(True).returncode, 0)

    def test_missing_parent_refuses_cleanup_and_preserves_volumes(self):
        self.save_plan()
        state = json.loads(self.state.read_text())
        del state["rows"]["zfspool/k8s"]
        self.write(state)
        result = self.run_helper(True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Required storage filesystem is missing", result.stderr)
        self.assertEqual(json.loads(self.state.read_text()), state)

    def test_changed_guid_refuses_deletion(self):
        self.save_plan()
        state = json.loads(self.state.read_text())
        state['rows'][A][0] = '999'
        self.write(state)
        self.assertNotEqual(self.run_helper(True).returncode, 0)
        self.assertIn(A, json.loads(self.state.read_text())['rows'])

    def test_external_clone_refuses_plan(self):
        state = json.loads(self.state.read_text())
        state['rows']['zfspool/photos'][3] = A + '@snapshot-a'
        self.write(state)
        result = self.run_helper()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('External clone', result.stderr)

    def test_unknown_openebs_child_refuses_plan(self):
        state = json.loads(self.state.read_text())
        state['rows']['zfspool/k8s/keep'] = ['7', 'filesystem', 'no', '-']
        self.write(state)
        self.assertNotEqual(self.run_helper().returncode, 0)

    def test_running_or_mounted_refuses_apply_but_allows_inventory(self):
        for change in ('service', 'kubelet', 'mount'):
            with self.subTest(change=change):
                state = json.loads(self.state.read_text())
                state['service'] = 'active' if change == 'service' else 'inactive'
                state['kubelet'] = change == 'kubelet'
                state['rows'][A][2] = 'yes' if change == 'mount' else 'no'
                self.write(state)
                self.save_plan()
                self.assertNotEqual(self.run_helper(True).returncode, 0)
                self.assertIn(A, json.loads(self.state.read_text())['rows'])


class HostpathTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("clean_storage", HELPER)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_inventory_records_identity_and_refuses_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            volume = root / A.rsplit('/', 1)[1]
            volume.mkdir()
            (volume / "payload").write_text("application data")
            entries = self.module.hostpath_inventory(directory)
            self.assertEqual(entries[str(volume)]["inode"], volume.stat().st_ino)
            (volume / "escape").symlink_to("/tmp")
            with self.assertRaisesRegex(ValueError, "symlink"):
                self.module.hostpath_inventory(directory)

    def test_known_driver_lock_preserved_but_unknown_files_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / ".openebs-quota.lock"
            lock.touch()
            self.assertEqual(self.module.hostpath_inventory(directory), {})
            self.assertTrue(lock.exists())
            (Path(directory) / "unexpected").touch()
            with self.assertRaisesRegex(ValueError, "Unrecognized"):
                self.module.hostpath_inventory(directory)

    def test_unrecognized_directory_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "important").mkdir()
            with self.assertRaisesRegex(ValueError, "Unrecognized"):
                self.module.hostpath_inventory(directory)


if __name__ == '__main__':
    unittest.main()
