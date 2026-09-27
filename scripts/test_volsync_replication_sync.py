#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
#
# SPDX-License-Identifier: 0BSD

"""Exercise backup orchestration through a simulated Kubernetes command interface."""

import copy
import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('sync', Path(__file__).with_name('volsync-replication-sync.py'))
SYNC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SYNC)


def source(name):
    return {'metadata': {'namespace': 'apps', 'name': name, 'uid': name, 'resourceVersion': '1'},
            'spec': {'sourcePVC': name, 'restic': {}, 'trigger': {'schedule': '0 * * * *'}},
            'status': {'lastSyncTime': '2026-09-27T00:00:00Z',
                       'latestMoverStatus': {'result': 'Successful'},
                       'conditions': [{'type': 'Synchronizing', 'status': 'False'}]}}


class API:
    def __init__(self, modes=None):
        self.sources = {n: source(n) for n in ('one', 'two')}
        self.modes = modes or {}
        self.patches = []
        self.steps = {}
        self.clock = 0
        self.busy_observed = False
        self.cleanup_conflict = False

    def sleep(self, seconds):
        self.clock += max(seconds, 1)

    def __call__(self, *args):
        if args[0] == 'get':
            return {'items': copy.deepcopy(list(self.sources.values()))}
        name = args[4]
        if args[2:4] == ('get', 'pvc'):
            return {'metadata': {'annotations': {'volsync.backube/latest-copy-status': 'WaitingForTrigger'}}}
        obj = self.sources[name]
        mode = self.modes.get(name)
        if args[2] == 'patch':
            operations = __import__('json').loads(args[args.index('-p') + 1])
            self.patches.append((name, copy.deepcopy(operations)))
            if operations[-1]['op'] == 'remove':
                if mode == 'cleanup-conflict' and not self.cleanup_conflict:
                    self.cleanup_conflict = True
                    raise SYNC.SyncError('conflict')
                obj['spec']['trigger'].pop('manual', None)
            else:
                if obj['status'].get('lastSyncStartTime'):
                    raise AssertionError('patched a running backup')
                obj['spec']['trigger']['manual'] = operations[-1]['value']
                self.steps[name] = 0
            return copy.deepcopy(obj)
        if name not in self.steps:
            if mode == 'busy' and not self.busy_observed:
                self.busy_observed = True
                result = copy.deepcopy(obj)
                result['status']['lastSyncStartTime'] = '2026-09-27T00:01:00Z'
                return result
            return copy.deepcopy(obj)
        step = self.steps[name]
        self.steps[name] += 1
        if mode == 'interrupt' and step == 0:
            raise KeyboardInterrupt()
        if mode == 'foreign' and step == 0:
            obj['spec']['trigger']['manual'] = 'foreign'
            return copy.deepcopy(obj)
        if mode == 'replaced' and step == 0:
            obj['metadata']['uid'] = 'replacement'
            return copy.deepcopy(obj)
        if step == 0 or mode in ('timeout', 'failure'):
            obj['status']['lastSyncStartTime'] = '2026-09-27T00:02:00Z'
            obj['status']['conditions'][0]['status'] = 'True'
            if mode == 'failure':
                obj['status']['latestMoverStatus'] = {'result': 'Failed', 'logs': 'new failure'}
        else:
            obj['status'].pop('lastSyncStartTime', None)
            obj['status']['conditions'][0]['status'] = 'False'
            obj['status']['lastManualSync'] = obj['spec']['trigger'].get('manual', '')
            obj['status']['latestMoverStatus'] = {'result': 'Successful'}
            if mode != 'stale':
                obj['status']['lastSyncTime'] = '2026-09-27T00:03:00Z'
        return copy.deepcopy(obj)


class TriggerTests(unittest.TestCase):
    def run_sync(self, api, error=None):
        output = io.StringIO()
        with patch.object(SYNC, 'kubectl', api), patch.object(SYNC.time, 'monotonic', lambda: api.clock), \
                patch.object(SYNC.time, 'sleep', api.sleep), patch('sys.stdout', output):
            if error:
                with self.assertRaises(error):
                    SYNC.synchronize(20)
            else:
                SYNC.synchronize(20)
        return output.getvalue()

    def assert_schedule_preserved(self, api):
        for obj in api.sources.values():
            self.assertEqual(obj['spec']['trigger'], {'schedule': '0 * * * *'})

    def test_all_sources_complete_with_progress_and_cleanup(self):
        api = API()
        output = self.run_sync(api)
        self.assertIn('waiting for quiesce hook', output)
        self.assertIn('2/2 fresh backups completed', output)
        self.assert_schedule_preserved(api)
        self.assertTrue(all(ops[0]['path'] == '/metadata/uid' for _, ops in api.patches))

    def test_waits_for_running_backup_before_requesting_new_one(self):
        api = API({'one': 'busy'})
        output = self.run_sync(api)
        self.assertIn('waiting for existing run', output)
        self.assert_schedule_preserved(api)

    def test_stale_success_is_not_accepted(self):
        api = API({'one': 'stale'})
        output = self.run_sync(api, SYNC.SyncError)
        self.assertIn('1/2 fresh backups completed', output)
        self.assert_schedule_preserved(api)

    def test_new_failure_does_not_prevent_waiting_for_other_sources(self):
        api = API({'one': 'failure'})
        output = self.run_sync(api, SYNC.SyncError)
        self.assertIn('apps/two: completed', output)
        self.assert_schedule_preserved(api)

    def test_old_failure_does_not_fail_a_new_success(self):
        api = API()
        api.sources['one']['status']['latestMoverStatus'] = {'result': 'Failed'}
        self.run_sync(api)
        self.assert_schedule_preserved(api)

    def test_timeout_reports_heartbeat_and_restores_schedules(self):
        api = API({'one': 'timeout'})
        output = self.run_sync(api, SYNC.SyncError)
        self.assertIn('1 waiting', output)
        self.assertIn('TIMED OUT', output)
        self.assert_schedule_preserved(api)

    def test_interrupt_restores_our_trigger(self):
        api = API({'one': 'interrupt'})
        self.run_sync(api, KeyboardInterrupt)
        self.assert_schedule_preserved(api)

    def test_foreign_trigger_is_not_removed(self):
        api = API({'one': 'foreign'})
        self.run_sync(api, SYNC.SyncError)
        self.assertEqual(api.sources['one']['spec']['trigger']['manual'], 'foreign')

    def test_replacement_source_is_not_mutated_during_cleanup(self):
        api = API({'one': 'replaced'})
        self.run_sync(api, SYNC.SyncError)
        self.assertEqual(len([1 for name, _ in api.patches if name == 'one']), 1)

    def test_cleanup_conflict_retries(self):
        api = API({'one': 'cleanup-conflict'})
        self.run_sync(api)
        self.assertTrue(api.cleanup_conflict)
        self.assert_schedule_preserved(api)

    def test_rejects_manual_override_before_any_patch(self):
        api = API()
        api.sources['two']['spec']['trigger']['manual'] = 'old'
        self.run_sync(api, SYNC.SyncError)
        self.assertFalse(api.patches)

    def test_rejects_paused_or_unscheduled_sources_before_any_patch(self):
        for change in ({'paused': True}, {'trigger': {}}):
            api = API()
            api.sources['two']['spec'].update(change)
            self.run_sync(api, SYNC.SyncError)
            self.assertFalse(api.patches)

    def test_progress_reports_running_mover_without_quiesce_annotations(self):
        obj = source('one')
        obj['status']['conditions'][0]['status'] = 'True'
        replies = [
            {'metadata': {}},
            {'items': [{'metadata': {'name': 'mover', 'ownerReferences': [{'uid': 'one'}]}}]},
            {'items': [{'status': {'phase': 'Running'}}]},
        ]
        with patch.object(SYNC, 'kubectl', side_effect=replies):
            self.assertEqual(SYNC.progress(obj), 'restic backup mover running')

    def test_empty_selection_is_an_error(self):
        api = API()
        api.sources = {}
        self.run_sync(api, SYNC.SyncError)


if __name__ == '__main__':
    unittest.main()
