# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT
"""Exercise automatic restore-image refresh through the Kubernetes CLI."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

FAKE = r'''#!/usr/bin/python3
import json, os, pathlib, sys
p = pathlib.Path(os.environ['STATE'])
s = json.loads(p.read_text())
a = sys.argv[3:]
s['calls'].append(a)
mode = s['mode']
code = 0
out = ''
if a[0] == 'get':
    if a[1] == 'replicationsource':
        out = json.dumps(s['source'])
    else:
        if s.get('requested'):
            if mode == 'replaced': s['rd']['metadata']['uid'] = 'replacement'
            if mode != 'timeout':
                s['rd']['status'].update({'lastManualSync': s['rd']['spec']['trigger']['manual'],
                    'latestImage': {'name': 'fresh-image'}, 'latestMoverStatus': {'result': 'Successful'}})
        out = json.dumps(s['rd'])
elif a[0] == 'patch':
    patch = json.loads(a[a.index('-p') + 1])
    if '--type=merge' in a:
        assert patch['metadata']['resourceVersion'] == s['rd']['metadata']['resourceVersion']
        if mode == 'conflict': code = 1
        else:
            s['rd']['metadata'].setdefault('annotations', {}).update(patch['metadata']['annotations'])
            s['rd']['spec']['trigger'].update(patch['spec']['trigger'])
            s['requested'] = True
    else:
        assert s['rd']['status']['latestImage']['name'] == 'fresh-image', 'cleanup before fresh image'
        for op in patch:
            parts = [part.replace('~1', '/') for part in op['path'].strip('/').split('/')]
            parent = s['rd']
            for part in parts[:-1]: parent = parent[part]
            key = parts[-1]
            if op['op'] == 'test': assert parent[key] == op['value']
            elif op['op'] == 'remove': del parent[key]
            else: parent[key] = op['value']
p.write_text(json.dumps(s))
print(out)
sys.exit(code)
'''
KEY = 'ops.soupglasses.dev/recovery-operation'
CHECKPOINT = '2026-10-22T06:15:00Z'


class RefreshTests(unittest.TestCase):
    def invoke(self, mode='success', source_changes=None, rd_changes=None):
        source = {'spec': {'sourcePVC': 'demo', 'restic': {}, 'trigger': {'schedule': '0 * * * *'}},
                  'status': {'lastSyncTime': CHECKPOINT, 'lastManualSync': 'completed-maintenance-request', 'latestMoverStatus': {'result': 'Successful'}}}
        rd = {'metadata': {'uid': 'original', 'resourceVersion': '1', 'annotations': {}},
              'spec': {'trigger': {'manual': 'restore-once'}, 'restic': {}},
              'status': {'lastManualSync': 'restore-once', 'latestImage': {'name': 'old-image'}}}
        if source_changes: source_changes(source)
        if rd_changes: rd_changes(rd)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tool = root / 'kubectl'
            tool.write_text(FAKE)
            tool.chmod(0o755)
            state = root / 'state.json'
            state.write_text(json.dumps({'source': source, 'rd': rd, 'mode': mode, 'calls': []}))
            result = subprocess.run(['bash', str(Path(__file__).resolve().parents[1] / 'kubernetes/components/volsync/refresh.sh')],
                env={**os.environ, 'PATH': f'{root}:/usr/bin:/bin', 'STATE': str(state), 'APP': 'demo', 'NS': 'default',
                     'REFRESH_TIMEOUT_SECONDS': '1' if mode == 'timeout' else '10', 'REFRESH_POLL_SECONDS': '0.01'},
                capture_output=True, text=True, timeout=15)
            return result, json.loads(state.read_text())

    def test_success_records_backup_only_after_own_fresh_image(self):
        result, state = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state['rd']['metadata']['annotations']['ops.soupglasses.dev/restore-checkpoint'], CHECKPOINT)
        self.assertNotIn(KEY, state['rd']['metadata']['annotations'])
        self.assertEqual(state['rd']['spec']['trigger']['manual'], 'backup-refresh-' + CHECKPOINT)

    def test_no_duplicate_restore_for_the_same_checkpoint(self):
        result, state = self.invoke(rd_changes=lambda o: o['metadata']['annotations'].update({'ops.soupglasses.dev/restore-checkpoint': CHECKPOINT}))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(state.get('requested', False))

    def test_paused_active_failed_or_unbacked_sources_do_not_refresh(self):
        changes = [lambda o: o['spec'].update({'paused': True}),
                   lambda o: o['status'].update({'lastSyncStartTime': 'running'}),
                   lambda o: o['status'].update({'latestMoverStatus': {'result': 'Failed'}}),
                   lambda o: o['spec'].update({'sourcePVC': 'another'})]
        for change in changes:
            with self.subTest(change=change):
                result, state = self.invoke(source_changes=change)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(state.get('requested', False))

    def test_explicit_recovery_historical_and_pending_requests_are_preserved(self):
        changes = [lambda o: o['metadata']['annotations'].update({KEY: 'refresh-human'}),
                   lambda o: o['spec']['restic'].update({'restoreAsOf': CHECKPOINT}),
                   lambda o: o['spec'].update({'paused': True}),
                   lambda o: o['spec']['trigger'].update({'manual': 'human-pending'})]
        for change in changes:
            with self.subTest(change=change):
                result, state = self.invoke(rd_changes=change)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(state.get('requested', False))

    def test_timeout_and_replacement_do_not_publish_a_checkpoint(self):
        for mode in ['timeout', 'replaced', 'conflict']:
            with self.subTest(mode=mode):
                result, state = self.invoke(mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('ops.soupglasses.dev/restore-checkpoint', state['rd']['metadata']['annotations'])
                if mode == 'timeout': self.assertIn(KEY, state['rd']['metadata']['annotations'])

    def test_retry_finishes_its_original_checkpoint_before_newer_backups(self):
        def pending(o):
            token = 'backup-refresh-2026-10-21T06:15:00Z'
            o['metadata']['annotations'].update({KEY: token, 'ops.soupglasses.dev/previous-restore-image': 'old-image'})
            o['spec']['trigger']['manual'] = token
            o['status'].update({'lastManualSync': token, 'latestMoverStatus': {'result': 'Successful'}, 'latestImage': {'name': 'fresh-image'}})
        result, state = self.invoke(rd_changes=pending)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state['rd']['metadata']['annotations']['ops.soupglasses.dev/restore-checkpoint'], '2026-10-21T06:15:00Z')
        self.assertFalse(state.get('requested', False))


if __name__ == '__main__':
    unittest.main()
