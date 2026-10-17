# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT
"""Exercise the restore CLI without a cluster or storage mutations."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

FAKE = r"""#!/usr/bin/python3
import json, os, pathlib, sys
p = pathlib.Path(os.environ['STATE'])
s = json.loads(p.read_text())
a = sys.argv[1:]
s['calls'].append(a)
a = a[2:]
code = 0
out = ''
if a[0] == 'get' and a[1] == 'deployment/demo':
    out = '1'
elif a[0] == 'get' and a[1] == 'replicationdestination':
    if '-o' in a:
        if s.get('requested'):
            if s['mode'] == 'failure':
                p.write_text(json.dumps(s))
                sys.exit(1)
            s['rd']['status'] = {
                'latestImage': {'name': 'fresh'},
                'lastManualSync': s['rd']['spec']['trigger']['manual'],
                'latestMoverStatus': {'result': 'Successful'},
                'conditions': [{'type': 'Synchronizing', 'status': 'False'}]}
        out = json.dumps(s['rd'])
elif a[0] == 'patch':
    patch = json.loads(a[a.index('-p') + 1])
    if '--type=merge' in a:
        s['rd']['spec']['trigger'].update(patch['spec']['trigger'])
        s['rd']['spec']['restic'].pop('restoreAsOf', None)
        if patch['spec']['restic']['restoreAsOf'] is not None:
            s['rd']['spec']['restic']['restoreAsOf'] = patch['spec']['restic']['restoreAsOf']
        s['requested'] = True
        s['token'] = patch['spec']['trigger']['manual']
    else:
        if s['mode'] == 'concurrent':
            s['rd']['spec']['trigger']['manual'] = 'human-request'
        assert s.get('bound'), 'cleanup before PVC bound'
        assert s.get('scaled_back'), 'cleanup before workload restarted'
        if s['mode'] == 'selection-concurrent':
            s['rd']['spec']['restic']['restoreAsOf'] = 'human-selection'
        for op in patch:
            if op['op'] == 'test':
                actual = s['rd']
                for part in op['path'].strip('/').split('/'):
                    actual = actual[part]
                if actual != op['value']:
                    code = 1
                    break
            elif op['op'] == 'remove':
                target = s['rd']
                parts = op['path'].strip('/').split('/')
                for part in parts[:-1]: target = target[part]
                del target[parts[-1]]
            elif op['op'] == 'add':
                target = s['rd']
                parts = op['path'].strip('/').split('/')
                for part in parts[:-1]: target = target[part]
                target[parts[-1]] = op['value']
elif a[0] == 'delete' and a[1] == 'pvc':
    assert s['rd']['spec']['trigger']['manual'] == s['token'], 'cron unfrozen before replacement'
    s['selection_at_delete'] = s['rd']['spec']['restic'].get('restoreAsOf')
elif a[0] == 'wait' and any('phase' in value for value in a):
    if s['mode'] == 'bind-failure': code = 1
    else: s['bound'] = True
elif a[0] == 'scale' and '--replicas=1' in a:
    s['scaled_back'] = True
elif a[0] == 'create':
    sys.stdin.read()
p.write_text(json.dumps(s))
print(out)
sys.exit(code)
"""


class RestoreTests(unittest.TestCase):
    def run_restore(self, mode, manual=None, before=None, original_before=None, confirm=True):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "kubectl"
            executable.write_text(FAKE)
            executable.chmod(0o755)
            trigger = {"schedule": "15 8 * * *"}
            if manual is not None:
                trigger["manual"] = manual
            state = root / "state.json"
            state.write_text(
                json.dumps(
                    {
                        "mode": mode,
                        "calls": [],
                        "rd": {
                            "metadata": {"uid": "original-rd"},
                            "spec": {"trigger": trigger, "restic": ({"restoreAsOf": original_before} if original_before else {})},
                            "status": {"latestImage": {"name": "old"}},
                        },
                    }
                )
            )
            result = subprocess.run(
                ["bash", str(Path(__file__).with_name("volsync-restore.sh")), "demo"] + ([before] if before else []),
                env={
                    **os.environ,
                    "PATH": f"{root}:/usr/bin:/bin",
                    "STATE": str(state),
                    "YES": "1" if confirm else "0",
                    "FLUX_KS": "",
                },
                input="n\n" if not confirm else None,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result, json.loads(state.read_text())

    def test_scheduled_success_resumes_cron_after_own_completed_request(self):
        result, state = self.run_restore("success")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("manual", state["rd"]["spec"]["trigger"])
        self.assertEqual(state["rd"]["spec"]["trigger"]["schedule"], "15 8 * * *")
        self.assertEqual(state["rd"]["status"]["lastManualSync"], state["token"])
        self.assertTrue(any("delete" in call for call in state["calls"]))

    def test_concurrent_request_is_not_removed_after_pvc_is_bound(self):
        result, state = self.run_restore("concurrent")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(state["rd"]["spec"]["trigger"]["manual"], "human-request")
        self.assertTrue(state["bound"])
        self.assertIn("cleanup conflicted or failed", result.stderr)

    def test_unproven_completion_keeps_trigger_and_reports_cleanup(self):
        result, state = self.run_restore("failure")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(state["rd"]["spec"]["trigger"]["manual"], state["token"])
        self.assertFalse(any("--type=json" in call for call in state["calls"]))
        self.assertIn("restore and trigger cleanup were not completed", result.stderr)

    def test_historical_selection_is_frozen_until_bound_then_removed(self):
        result, state = self.run_restore("success", before="2026-10-01T00:00:00Z")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state["selection_at_delete"], "2026-10-01T00:00:00Z")
        self.assertNotIn("restoreAsOf", state["rd"]["spec"]["restic"])

    def test_original_historical_selection_is_restored(self):
        result, state = self.run_restore("success", before="2026-10-01T00:00:00Z", original_before="2026-09-01T00:00:00Z")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state["rd"]["spec"]["restic"]["restoreAsOf"], "2026-09-01T00:00:00Z")

    def test_prompt_abort_keeps_manual_freeze_and_selection(self):
        result, state = self.run_restore("success", before="2026-10-01T00:00:00Z", confirm=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(state["rd"]["spec"]["trigger"]["manual"], state["token"])
        self.assertEqual(state["rd"]["spec"]["restic"]["restoreAsOf"], "2026-10-01T00:00:00Z")
        self.assertFalse(any("delete" in call for call in state["calls"]))
        self.assertIn("replacement PVC", result.stderr)

    def test_bind_failure_keeps_manual_freeze_and_selection(self):
        result, state = self.run_restore("bind-failure", before="2026-10-01T00:00:00Z")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(state["rd"]["spec"]["trigger"]["manual"], state["token"])
        self.assertEqual(state["rd"]["spec"]["restic"]["restoreAsOf"], "2026-10-01T00:00:00Z")

    def test_concurrent_selection_is_not_replaced_or_unfrozen(self):
        result, state = self.run_restore("selection-concurrent", before="2026-10-01T00:00:00Z")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(state["rd"]["spec"]["restic"]["restoreAsOf"], "human-selection")
        self.assertEqual(state["rd"]["spec"]["trigger"]["manual"], state["token"])

    def test_existing_manual_request_is_not_restored_or_removed(self):
        result, state = self.run_restore("success", manual="restore-once")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state["rd"]["spec"]["trigger"]["manual"], state["token"])
        self.assertNotEqual(state["token"], "restore-once")
        self.assertFalse(any("--type=json" in call for call in state["calls"]))


if __name__ == "__main__":
    unittest.main()
