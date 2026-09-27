#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: 0BSD
"""Exercise uninstall preparation through the Kubernetes command interface."""
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.test_volsync_replication_sync import API

SPEC = importlib.util.spec_from_file_location('hold', Path(__file__).with_name('volsync-uninstall-hold.py'))
HOLD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HOLD)


class MaintenanceAPI(API):
    def __init__(self, modes=None):
        super().__init__(modes)
        self.deployments = {n: {'metadata': {'name': n, 'uid': n, 'resourceVersion': '1'},
                               'spec': {'replicas': 1}} for n in HOLD.CONTROLLERS}
        self.actions = []
        self.cleanup_reads = 0
        self.cluster = 'original-cluster'

    def __call__(self, *args):
        self.actions.append(args)
        if args[:3] == ('get', 'namespace', 'kube-system'):
            return {'metadata': {'uid': self.cluster}}
        if args[:3] == ('-n', 'flux-system', 'get') and args[3] == 'deployment':
            return copy.deepcopy(self.deployments[args[4]])
        if args[:3] == ('-n', 'flux-system', 'patch'):
            obj = self.deployments[args[4]]
            operations = json.loads(args[args.index('-p') + 1])
            self.assert_test(obj, operations)
            obj['spec']['replicas'] = operations[-1]['value']
            return copy.deepcopy(obj)
        if args[:4] == ('-n', 'flux-system', 'get', 'pods'):
            return {'items': []}
        if args[:2] == ('get', 'jobs'):
            self.cleanup_reads += 1
            return {'items': [{'metadata': {'ownerReferences': [{'uid': 'one'}]}}]
                    if self.cleanup_reads == 1 else []}
        if args[:2] == ('get', 'pods'):
            return {'items': []}
        return super().__call__(*args)

    @staticmethod
    def assert_test(obj, operations):
        for op in operations[:-1]:
            value = obj
            for key in op['path'].strip('/').split('/'):
                value = value[key]
            if value != op['value']:
                raise HOLD.sync.SyncError('conflict')


class MaintenanceTests(unittest.TestCase):
    def exercise(self, api, callback):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / 'state.json'
            with patch.object(HOLD.sync, 'kubectl', api), \
                    patch.object(HOLD.time, 'monotonic', lambda: api.clock), \
                    patch.object(HOLD.time, 'sleep', api.sleep), patch('sys.stdout', io.StringIO()):
                callback(state)

    def test_holds_backups_after_success_then_explicit_resume_restores(self):
        api = MaintenanceAPI()

        def check(path):
            HOLD.prepare(path, 30)
            self.assertTrue(path.exists())
            self.assertGreaterEqual(api.cleanup_reads, 2)
            self.assertTrue(all(d['spec']['replicas'] == 0 for d in api.deployments.values()))
            self.assertTrue(all(s['spec']['trigger']['manual'] == s['status']['lastManualSync']
                                for s in api.sources.values()))
            HOLD.resume(path)
            self.assertFalse(path.exists())
            self.assertTrue(all(d['spec']['replicas'] == 1 for d in api.deployments.values()))
            self.assertTrue(all('manual' not in s['spec']['trigger'] for s in api.sources.values()))
        self.exercise(api, check)

    def test_backup_failure_resumes_flux_and_does_not_leave_hold(self):
        api = MaintenanceAPI({'one': 'failure'})

        def check(path):
            with self.assertRaises(HOLD.sync.SyncError):
                HOLD.prepare(path, 30)
            self.assertFalse(path.exists())
            self.assertTrue(all(d['spec']['replicas'] == 1 for d in api.deployments.values()))
            self.assertTrue(all('manual' not in s['spec']['trigger'] for s in api.sources.values()))
        self.exercise(api, check)

    def test_existing_journal_is_not_overwritten(self):
        api = MaintenanceAPI()

        def check(path):
            path.write_text('existing')
            with self.assertRaises(HOLD.sync.SyncError):
                HOLD.prepare(path, 30)
            self.assertEqual(path.read_text(), 'existing')
            self.assertFalse(api.actions)
        self.exercise(api, check)

    def test_zero_pods_does_not_hide_flux_desired_replica_race(self):
        api = MaintenanceAPI()

        def check(path):
            HOLD.prepare(path, 30)
            state = json.loads(path.read_text())
            api.deployments['kustomize-controller']['spec']['replicas'] = 1
            with self.assertRaisesRegex(HOLD.sync.SyncError, 'Flux writer changed'):
                HOLD.wait_quiet(state, api.clock + 30)
        self.exercise(api, check)

    def test_resume_can_retry_after_one_controller_already_resumed(self):
        api = MaintenanceAPI()

        def check(path):
            HOLD.prepare(path, 30)
            # Simulate losing the command response after the first scale succeeded.
            api.deployments['kustomize-controller']['spec']['replicas'] = 1
            HOLD.resume(path)
            self.assertFalse(path.exists())
            self.assertTrue(all(d['spec']['replicas'] == 1 for d in api.deployments.values()))
        self.exercise(api, check)

    def test_rebuilt_cluster_cannot_receive_old_maintenance_state(self):
        api = MaintenanceAPI()

        def check(path):
            HOLD.prepare(path, 30)
            api.cluster = 'rebuilt-cluster'
            actions = len(api.actions)
            with self.assertRaisesRegex(HOLD.sync.SyncError, 'another cluster'):
                HOLD.resume(path)
            self.assertTrue(path.exists())
            self.assertEqual(len(api.actions), actions + 1)
        self.exercise(api, check)

    def test_replaced_source_leaves_flux_held_and_journal_for_operator(self):
        api = MaintenanceAPI()

        def check(path):
            HOLD.prepare(path, 30)
            api.sources['one']['metadata']['uid'] = 'replacement'
            with self.assertRaises(HOLD.sync.SyncError):
                HOLD.resume(path)
            self.assertTrue(path.exists())
            self.assertTrue(all(d['spec']['replicas'] == 0 for d in api.deployments.values()))
        self.exercise(api, check)


if __name__ == '__main__':
    unittest.main()
