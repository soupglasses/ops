#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
#
# SPDX-License-Identifier: 0BSD

"""Trigger existing restic ReplicationSources and report progress until all finish.

Uses VolSync's manual-trigger contract, not the CLI's rsync relationship manager:
https://volsync.readthedocs.io/en/stable/usage/triggers.html
"""

import argparse
import datetime
import json
import re
import signal
import subprocess
import sys
import time
import uuid

RESOURCE = 'replicationsources.volsync.backube'


class SyncError(Exception):
    pass


def kubectl(*args):
    result = subprocess.run(['kubectl', '--request-timeout=15s', *args],
                            capture_output=True, text=True, timeout=20)
    if result.returncode:
        # Mover logs and API diagnostics can contain repository details.
        raise SyncError('kubectl failed; check connectivity and permissions')
    return json.loads(result.stdout) if result.stdout.strip() else {}


def identity(source):
    meta = source['metadata']
    return meta['namespace'], meta['name']


def read_source(key):
    return kubectl('-n', key[0], 'get', RESOURCE, key[1], '-o', 'json')


def syncing(source):
    status = source.get('status', {})
    return bool(status.get('lastSyncStartTime')) or any(
        c['type'] == 'Synchronizing' and c['status'] == 'True'
        for c in status.get('conditions', []))


def idle(source):
    trigger = source['spec'].get('trigger', {}).get('manual')
    return not syncing(source) and (
        not trigger or source.get('status', {}).get('lastManualSync') == trigger)


def validate(source, uid=None):
    meta, spec = source['metadata'], source['spec']
    if meta.get('deletionTimestamp') or (uid and meta['uid'] != uid):
        raise SyncError('source was deleted or replaced')
    if spec.get('paused'):
        raise SyncError('source is paused')
    if not spec.get('trigger', {}).get('schedule'):
        raise SyncError('source has no schedule; only scheduled backups are supported')
    if 'restic' not in spec:
        raise SyncError('source is not a restic backup')


def patch(source, operations):
    meta = source['metadata']
    return kubectl('-n', meta['namespace'], 'patch', RESOURCE, meta['name'],
                   '--type=json', '-p', json.dumps([
                       {'op': 'test', 'path': '/metadata/uid', 'value': meta['uid']},
                       {'op': 'test', 'path': '/metadata/resourceVersion',
                        'value': meta['resourceVersion']}, *operations]), '-o', 'json')


def restore_trigger_once(original, token):
    current = read_source(identity(original))
    if current['metadata']['uid'] != original['metadata']['uid']:
        raise SyncError('source replaced; refusing trigger cleanup')
    if current['spec'].get('trigger', {}).get('manual') != token:
        return  # Flux or another operator owns the current value.
    patch(current, [{'op': 'remove', 'path': '/spec/trigger/manual'}])


def restore_trigger(original, token):
    # Controller status updates can conflict with cleanup; re-read ownership each time.
    for attempt in range(3):
        try:
            restore_trigger_once(original, token)
            return
        except SyncError:
            if attempt == 2:
                raise
            time.sleep(1)


def progress(source):
    key = identity(source)
    status = source.get('status', {})
    if not syncing(source):
        return 'waiting for VolSync'
    pvc = kubectl('-n', key[0], 'get', 'pvc', source['spec']['sourcePVC'], '-o', 'json')
    copy = pvc['metadata'].get('annotations', {}).get('volsync.backube/latest-copy-status')
    if copy == 'WaitingForTrigger':
        return 'preparing application data; waiting for quiesce hook'
    reason = next((c.get('reason', '') for c in status.get('conditions', [])
                   if c['type'] == 'Synchronizing'), '')
    if reason == 'CleaningUp':
        return 'finishing backup and cleaning up'
    if copy == 'Error' or reason == 'Error':
        return 'controller reports an error; waiting for completion or timeout'
    jobs = kubectl('-n', key[0], 'get', 'jobs', '-l',
                   'app.kubernetes.io/created-by=volsync', '-o', 'json')['items']
    for job in jobs:
        if not any(ref.get('uid') == source['metadata']['uid']
                   for ref in job['metadata'].get('ownerReferences', [])):
            continue
        if job.get('status', {}).get('succeeded'):
            return 'backup mover finished; waiting for controller cleanup'
        pods = kubectl('-n', key[0], 'get', 'pods', '-l',
                       'job-name=' + job['metadata']['name'], '-o', 'json')['items']
        for pod in pods:
            state = pod.get('status', {})
            waiting = [c.get('state', {}).get('waiting', {}).get('reason')
                       for c in state.get('containerStatuses', [])]
            if any(waiting):
                return 'waiting for backup mover: ' + ', '.join(filter(None, waiting))
            if state.get('phase') == 'Running':
                return 'restic backup mover running'
            if state.get('phase') == 'Succeeded':
                return 'backup mover finished; waiting for controller cleanup'
        return 'waiting for backup mover Pod'
    return 'preparing snapshot / starting backup mover'


def newer(current, previous):
    try:
        current = datetime.datetime.fromisoformat(current.replace('Z', '+00:00'))
        return not previous or current > datetime.datetime.fromisoformat(previous.replace('Z', '+00:00'))
    except (ValueError, TypeError):
        raise SyncError('invalid backup completion timestamp') from None


def duration(value):
    match = re.fullmatch(r'([1-9][0-9]*)([smh]?)', value)
    if not match:
        raise argparse.ArgumentTypeError('use a positive duration such as 30m or 2h')
    return int(match[1]) * {'': 1, 's': 1, 'm': 60, 'h': 3600}[match[2]]


def synchronize(timeout, namespace=None, interval=3, hold=False, on_request=None):
    scope = ['-n', namespace] if namespace else ['-A']
    sources = kubectl('get', RESOURCE, *scope, '-o', 'json')['items']
    if not sources:
        raise SyncError('No ReplicationSources found; no backups were run')
    sources.sort(key=identity)
    for source in sources:
        validate(source)  # Check all sources before mutating any of them.
        if source['spec'].get('trigger', {}).get('manual'):
            raise SyncError('/'.join(identity(source)) + ': existing manual override; finish and remove it first')
    token = 'ops-' + uuid.uuid4().hex
    started = time.monotonic()
    deadline = started + timeout
    pending = {identity(s): s for s in sources}
    requested, completed, failed, shown = {}, {}, {}, {}

    def show(key, message):
        if shown.get(key) != message:
            print(f'[{int(time.monotonic() - started):4d}s] {key[0]}/{key[1]}: {message}', flush=True)
            shown[key] = message

    print(f'Triggering {len(sources)} backups; timeout {timeout}s. Request {token}', flush=True)
    heartbeat = started
    successful = False
    try:
        while pending and time.monotonic() < deadline:
            for key, original in list(pending.items()):
                try:
                    current = read_source(key)
                    validate(current, original['metadata']['uid'])
                    status = current.get('status', {})
                    if key not in requested:
                        if current['spec'].get('trigger', {}).get('manual'):
                            raise SyncError('another manual request appeared; leaving it untouched')
                        if not idle(current):
                            show(key, 'waiting for existing run before requesting a fresh backup')
                            continue
                        # VolSync can acknowledge a new token on an already-running
                        # sync. Wait for idle and atomically test that state before patching.
                        operations = []
                        if 'trigger' not in current['spec']:
                            operations.append({'op': 'add', 'path': '/spec/trigger', 'value': {}})
                        operations.append({'op': 'add', 'path': '/spec/trigger/manual', 'value': token})
                        requested[key] = current  # Also clean up an ambiguous patch response.
                        if on_request:
                            on_request(current, token)
                        patch(current, operations)
                        show(key, 'fresh backup requested')
                        continue
                    if status.get('lastManualSync') == token:
                        if syncing(current):
                            show(key, 'backup completed; waiting for controller cleanup')
                            continue
                        if (status.get('latestMoverStatus', {}).get('result') != 'Successful'
                                or not status.get('lastSyncTime')
                                or not newer(status['lastSyncTime'], requested[key].get('status', {}).get('lastSyncTime'))):
                            raise SyncError('run completed without a fresh successful backup')
                        completed[key] = status['lastSyncTime']
                        show(key, f"completed at {status['lastSyncTime']} ({status.get('lastSyncDuration', 'duration unavailable')})")
                        del pending[key]
                        continue
                    if current['spec'].get('trigger', {}).get('manual') != token:
                        raise SyncError('manual request was changed or removed before completion; rerun')
                    previous = requested[key].get('status', {}).get('latestMoverStatus', {})
                    mover = status.get('latestMoverStatus', {})
                    if (status.get('lastSyncStartTime') and mover.get('result') == 'Failed'
                            and mover != previous):
                        raise SyncError('the new backup mover failed; inspect its Job')
                    show(key, progress(current))
                except (SyncError, subprocess.TimeoutExpired) as error:
                    failed[key] = str(error)
                    show(key, 'FAILED: ' + str(error))
                    del pending[key]
            now = time.monotonic()
            if now - heartbeat >= 15 and pending:
                print(f'[{int(now - started):4d}s] {len(completed)}/{len(sources)} completed; '
                      f'{len(pending)} waiting; {len(failed)} failed', flush=True)
                heartbeat = now
            if pending:
                time.sleep(min(interval, max(0, deadline - time.monotonic())))
        for key in pending:
            failed[key] = 'timed out'
            show(key, 'TIMED OUT; any active controller job continues')
        successful = not failed
    finally:
        for key, original in requested.items():
            if hold and successful:
                continue
            try:
                restore_trigger(original, token)
            except (SyncError, subprocess.TimeoutExpired) as error:
                failed[key] = 'trigger cleanup failed'
                show(key, 'CLEANUP FAILED: ' + str(error))
    print(f'{len(completed)}/{len(sources)} fresh backups completed; {len(failed)} failed.', flush=True)
    if failed:
        raise SyncError('Not all backups completed successfully; do not proceed with recovery testing')
    if hold:
        print('Backups held at their completed manual triggers; schedules cannot start another run.', flush=True)
    print('Backup jobs completed. Restore validation is still required.', flush=True)
    return {'token': token, 'sources': list(requested.values())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=duration, default=1800)
    parser.add_argument('--namespace', help='limit to one namespace (default: all namespaces)')
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    synchronize(args.timeout, args.namespace)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Interrupted; controller jobs continue and quiesce hooks retain responsibility for resuming apps.', file=sys.stderr)
        sys.exit(130)
    except (SyncError, subprocess.TimeoutExpired) as exc:
        print(f'Backup request failed: {exc}', file=sys.stderr)
        sys.exit(1)
