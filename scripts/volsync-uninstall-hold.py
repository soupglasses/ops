#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: 0BSD
"""Finish backups and hold them before invoking the official RKE2 uninstaller.

The hold deliberately covers the cluster, including Flux reconciliation. It stays
in place after uninstall; a new cluster reconstructs normal schedules from Git.
Use resume only when cancelling maintenance on a surviving cluster.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

spec = importlib.util.spec_from_file_location('volsync_sync', Path(__file__).with_name('volsync-replication-sync.py'))
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)
CONTROLLERS = ('kustomize-controller', 'helm-controller')


def save(path, state):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    with open(temp, 'w', encoding='utf-8') as stream:
        os.chmod(temp, 0o600)
        json.dump(state, stream)
    temp.replace(path)


def scale(original, replicas):
    meta = original['metadata']
    current = sync.kubectl('-n', 'flux-system', 'get', 'deployment', meta['name'], '-o', 'json')
    if current['metadata']['uid'] != meta['uid']:
        raise sync.SyncError('Flux controller was replaced; refusing to change it')
    if current['spec'].get('replicas', 1) == replicas:
        return current  # Retry an interrupted resume without scaling an already-restored writer.
    return sync.kubectl('-n', 'flux-system', 'patch', 'deployment', meta['name'], '--type=json',
                        '-p', json.dumps([
                            {'op': 'test', 'path': '/metadata/uid', 'value': meta['uid']},
                            {'op': 'test', 'path': '/metadata/resourceVersion',
                             'value': current['metadata']['resourceVersion']},
                            {'op': 'test', 'path': '/spec/replicas',
                             'value': original['spec'].get('replicas', 1) if replicas == 0 else 0},
                            {'op': 'add', 'path': '/spec/replicas', 'value': replicas}]), '-o', 'json')


def resume(path, state=None):
    state = state or json.loads(path.read_text())
    cluster = sync.kubectl('get', 'namespace', 'kube-system', '-o', 'json')['metadata']['uid']
    if state['cluster_uid'] != cluster:
        raise sync.SyncError('Journal belongs to another cluster; refusing to apply old backup state')
    # Restore triggers while Flux is still stopped. A failure leaves the journal
    # and reconciliation hold in place so it cannot silently unblock backups.
    for source in state['sources']:
        sync.restore_trigger(source, state['token'])
    for controller in state['controllers']:
        scale(controller, controller['spec'].get('replicas', 1))
    path.unlink()
    print('Backup schedules and Flux reconciliation resumed.', flush=True)


def stopped(state):
    for original in state['controllers']:
        current = sync.kubectl('-n', 'flux-system', 'get', 'deployment',
                               original['metadata']['name'], '-o', 'json')
        if (current['metadata']['uid'] != original['metadata']['uid'] or
                current['spec'].get('replicas', 1) != 0):
            raise sync.SyncError('Flux writer changed during maintenance; refusing uninstall')
    pods = sync.kubectl('-n', 'flux-system', 'get', 'pods', '-o', 'json')['items']
    return not any(p['metadata'].get('labels', {}).get('app') in CONTROLLERS
                   for p in pods)


def wait_quiet(state, deadline):
    owners = {s['metadata']['uid'] for s in state['sources']}
    while time.monotonic() < deadline:
        if not stopped(state):
            raise sync.SyncError('Flux writer restarted during maintenance; refusing uninstall')
        for original in state['sources']:
            current = sync.read_source(sync.identity(original))
            sync.validate(current, original['metadata']['uid'])
            if (current['spec'].get('trigger', {}).get('manual') != state['token'] or
                    current.get('status', {}).get('lastManualSync') != state['token'] or
                    sync.syncing(current)):
                raise sync.SyncError('A backup changed after the hold; refusing uninstall')
        jobs = sync.kubectl('get', 'jobs', '-A', '-o', 'json')['items']
        pods = sync.kubectl('get', 'pods', '-A', '-o', 'json')['items']
        mover_jobs = [j for j in jobs if any(r.get('uid') in owners
                      for r in j['metadata'].get('ownerReferences', []))]
        # Wait for controller cleanup, including the last completed mover Pod.
        if not mover_jobs and not any(p['metadata'].get('labels', {}).get(
                'app.kubernetes.io/created-by') == 'volsync' for p in pods):
            return
        print('Waiting for VolSync mover cleanup before uninstall...', flush=True)
        time.sleep(3)
    raise sync.SyncError('Timed out waiting for backup cleanup; uninstall was not started')


def prepare(path, timeout):
    if path.exists():
        raise sync.SyncError('A maintenance journal already exists; resume it before preparing again')
    cluster = sync.kubectl('get', 'namespace', 'kube-system', '-o', 'json')['metadata']['uid']
    state = {'controllers': [], 'sources': [], 'token': None, 'cluster_uid': cluster}
    deadline = time.monotonic() + timeout
    try:
        # Stop Flux's writers before holding individual sources. Merely suspending
        # children races their parent reconciliation; stopping both writers also
        # covers Helm-managed resources. Existing backup controllers keep running.
        for name in CONTROLLERS:
            controller = sync.kubectl('-n', 'flux-system', 'get', 'deployment', name, '-o', 'json')
            state['controllers'].append({
                'metadata': {k: controller['metadata'][k] for k in ('name', 'uid')},
                'spec': {'replicas': controller['spec'].get('replicas', 1)}})
            save(path, state)
            scale(controller, 0)
        while not stopped(state):
            if time.monotonic() >= deadline:
                raise sync.SyncError('Timed out waiting for Flux writers to stop')
            time.sleep(2)
        print('Flux reconciliation held; finishing fresh application backups.', flush=True)

        def record(source, token):
            state['token'] = token
            state['sources'].append({'metadata': {k: source['metadata'][k]
                                                   for k in ('name', 'namespace', 'uid')}})
            save(path, state)

        sync.synchronize(max(1, int(deadline - time.monotonic())), hold=True, on_request=record)
        wait_quiet(state, deadline)
        print('Backup hold established. Upstream uninstall may now run.', flush=True)
    except BaseException:
        if path.exists():
            try:
                resume(path, state)
            except BaseException:
                print(f'Automatic resume failed; hold journal retained at {path}.', file=sys.stderr)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'resume'])
    parser.add_argument('--state', type=Path, default=Path('/var/lib/rke2-uninstall/backup-hold.json'))
    parser.add_argument('--timeout', type=sync.duration, default=1800)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if args.action == 'prepare':
        prepare(args.state, args.timeout)
    else:
        resume(args.state)


if __name__ == '__main__':
    try:
        main()
    except (sync.SyncError, subprocess.TimeoutExpired, KeyboardInterrupt, OSError) as error:
        sys.exit(f'Maintenance failed: {error}')
