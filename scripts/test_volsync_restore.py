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
import json, os, pathlib, signal, sys
p = pathlib.Path(os.environ['STATE'])
s = json.loads(p.read_text())
a = sys.argv[1:]
s['calls'].append(a)
if a[:1] == ['-n']: a = a[2:]
code, out = 0, ''

def patch_object(obj, operations):
    for op in operations:
        parts = [v.replace('~1', '/').replace('~0', '~') for v in op['path'].strip('/').split('/')]
        parent = obj
        for part in parts[:-1]: parent = parent[part]
        key = parts[-1]
        if op['op'] == 'test':
            if parent.get(key) != op['value']: return 1
        elif op['op'] == 'remove': del parent[key]
        else: parent[key] = op['value']
    return 0

if a[0] == 'get':
    resource = a[1]
    if resource == 'volumesnapshotclass':
        out = 'Delete' if s['mode'] == 'delete-policy' else 'Retain'
    elif resource == 'replicationsource':
        out = '' if s['mode'] == 'unbacked' else 'demo'
    elif resource == 'pods':
        out = 'old-pod'
    elif resource == 'pvc':
        if any('metadata.uid' in v for v in a):
            out = 'concurrent-pvc' if s['mode'] == 'pvc-concurrent' else 'original-pvc'
        elif '-o' in a:
            out = json.dumps({'metadata': {'name': 'demo', 'namespace': 'default', 'uid': 'original-pvc'},
                'spec': {'volumeName': 'old-volume', 'dataSourceRef': {'kind': 'ReplicationDestination', 'name': 'demo-dst'}}})
    elif resource == 'replicationdestination':
        if s['mode'] == 'busy-destination': s['rd']['status']['lastSyncStartTime'] = 'running'
        if s['mode'] == 'locked-destination': s['rd']['metadata']['annotations'] = {'ops.soupglasses.dev/recovery-operation': 'other'}
        if s.get('requested') and '-o' in a:
            if s['mode'] == 'failure': code = 1
            else:
                s['rd']['status'] = {'latestImage': {'name': 'fresh'},
                    'lastManualSync': s['rd']['spec']['trigger']['manual'],
                    'latestMoverStatus': {'result': 'Successful'},
                    'conditions': [{'type': 'Synchronizing', 'status': 'False'}]}
        out = json.dumps(s['rd'])
    elif resource == 'deployment/demo':
        if any('replicas' in v for v in a): out = '1'
        elif '-o' in a:
            out = json.dumps({'metadata': {'annotations': {'meta.helm.sh/release-name': 'demo'}},
                'spec': {'selector': {'matchLabels': {'app': 'demo'}}}})
    elif resource in s['controls']:
        if s['mode'] == 'busy-source' and resource == 'replicationsource/demo': s['controls'][resource]['status']['lastSyncStartTime'] = 'running'
        if s['mode'] == 'already-held' and resource.startswith('helmrelease.'): s['controls'][resource]['spec']['suspend'] = True
        out = json.dumps(s['controls'][resource])
    else:
        raise AssertionError(('unexpected get', a))
elif a[0] == 'patch':
    patch = json.loads(a[a.index('-p') + 1])
    obj = s['rd'] if a[1] == 'replicationdestination' else s['controls'][a[1]]
    if '--type=merge' in a:
        obj['metadata'].setdefault('annotations', {}).update(patch['metadata'].get('annotations', {}))
        if obj is s['rd']:
            obj['spec']['trigger'].update(patch['spec']['trigger'])
            obj['spec']['restic'].pop('restoreAsOf', None)
            if patch['spec']['restic']['restoreAsOf'] is not None:
                obj['spec']['restic']['restoreAsOf'] = patch['spec']['restic']['restoreAsOf']
            s['requested'] = True
            s['token'] = patch['spec']['trigger']['manual']
        else: obj['spec'].update(patch['spec'])
    else:
        is_cleanup = any(op['path'] == '/spec/restic' for op in patch)
        if is_cleanup:
            assert s.get('bound'), 'cleanup before PVC bound'
            assert s.get('replicas') == 0, 'cleanup while writers running'
            if s['mode'] == 'concurrent': obj['spec']['trigger']['manual'] = 'human-request'
            if s['mode'] == 'selection-concurrent': obj['spec']['restic']['restoreAsOf'] = 'human-selection'
        elif obj is not s['rd']:
            assert s.get('verified'), 'controllers released before application verification'
        code = patch_object(obj, patch)
elif a[0] == 'delete' and a[1] == 'pvc':
    assert s['replicas'] == 0
    assert s.get('snapshot_ready'), 'delete without rollback snapshot'
    assert s['rd']['spec']['trigger']['manual'] == s['token'], 'cron unfrozen before replacement'
    s['selection_at_delete'] = s['rd']['spec']['restic'].get('restoreAsOf')
    s['deleted'] = True
elif a[0] == 'wait':
    if any('phase' in value for value in a):
        if s['mode'] == 'bind-failure': code = 1
        else: s['bound'] = True
    elif '--for=delete' in a:
        if s['mode'] == 'pod-failure': code = 1
    else:
        if s['mode'] == 'snapshot-failure': code = 1
        else: s['snapshot_ready'] = True
elif a[0] == 'scale':
    s['replicas'] = int(next(v.split('=')[1] for v in a if v.startswith('--replicas=')))
elif a[0] == 'rollout':
    if s['mode'] == 'rollout-failure': code = 1
elif a[0] == 'create':
    manifest = sys.stdin.read()
    if manifest.startswith('{'):
        claim = json.loads(manifest)
        assert 'volumeName' not in claim['spec']
        assert claim['spec']['dataSourceRef']['name'] == 'demo-dst'
        s['replacement'] = True
    else:
        assert s['replicas'] == 0
elif a[0] == 'verify':
    assert s['bound'] and s['replicas'] == 1
    if s['mode'] == 'validation-failure': code = 1
    elif s['mode'] == 'interrupted':
        p.write_text(json.dumps(s))
        os.kill(os.getppid(), signal.SIGTERM)
        sys.exit(1)
    else: s['verified'] = True
else:
    raise AssertionError(('unexpected command', a))
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
                        "controls": {
                            name: {"metadata": {"resourceVersion": "1"}, "spec": {}, "status": {}}
                            for name in ["replicationsource/demo", "kustomization.kustomize.toolkit.fluxcd.io/demo", "helmrelease.helm.toolkit.fluxcd.io/demo"]
                        },
                        "rd": {
                            "metadata": {"uid": "original-rd", "resourceVersion": "1"},
                            "spec": {"trigger": trigger, "restic": ({"restoreAsOf": original_before} if original_before else {})},
                            "status": {"latestImage": {"name": "old"}, "lastManualSync": manual or ""},
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
                    "FLUX_KS": "demo",
                    "VERIFY_COMMAND": "kubectl verify",
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
        self.assertTrue(state["verified"])


    def test_failures_keep_writers_stopped_and_controls_held(self):
        for mode in ["pod-failure", "snapshot-failure", "pvc-concurrent", "bind-failure", "rollout-failure", "validation-failure", "interrupted"]:
            with self.subTest(mode=mode):
                result, state = self.run_restore(mode)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(state["replicas"], 0)
                self.assertTrue(state["controls"]["replicationsource/demo"]["spec"]["paused"])
                self.assertTrue(state["controls"]["helmrelease.helm.toolkit.fluxcd.io/demo"]["spec"]["suspend"])
                if mode in ["pod-failure", "snapshot-failure", "pvc-concurrent"]:
                    self.assertFalse(state.get("deleted", False))

    def test_unbacked_workloads_and_nonretaining_snapshots_are_rejected(self):
        for mode in ["unbacked", "delete-policy", "busy-destination", "locked-destination"]:
            with self.subTest(mode=mode):
                result, state = self.run_restore(mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(state.get("requested", False))
                self.assertFalse(state.get("deleted", False))

    def test_historical_restore_does_not_leave_a_selection_on_manual_destination(self):
        result, state = self.run_restore("success", manual="restore-once", before="2026-10-01T00:00:00Z")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("restoreAsOf", state["rd"]["spec"]["restic"])
        self.assertEqual(state["rd"]["spec"]["trigger"]["manual"], state["token"])

    def test_active_backup_prevents_claim_replacement(self):
        result, state = self.run_restore("busy-source")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(state.get("deleted", False))
        self.assertNotIn("replicas", state)

    def test_preexisting_controller_suspension_is_preserved(self):
        result, state = self.run_restore("already-held")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(state["controls"]["helmrelease.helm.toolkit.fluxcd.io/demo"]["spec"]["suspend"])

    def test_success_releases_owned_holds_only_after_data_verification(self):
        result, state = self.run_restore("success")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(state["verified"])
        for name, obj in state["controls"].items():
            self.assertFalse(obj["spec"].get("paused", obj["spec"].get("suspend")))
            self.assertNotIn("ops.soupglasses.dev/recovery-operation", obj["metadata"].get("annotations", {}))
        self.assertNotIn("ops.soupglasses.dev/recovery-operation", state["rd"]["metadata"]["annotations"])


if __name__ == "__main__":
    unittest.main()
