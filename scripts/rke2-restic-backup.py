#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
#
# SPDX-License-Identifier: 0BSD

"""Encrypt an RKE2 snapshot off-cluster and verify a fresh native restore."""

import argparse
import datetime
import fcntl
import json
import os
import re
import signal
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path

RESTIC_IMAGE = "quay.io/backube/volsync@sha256:0d03a6aad57569eba2c0eaa0848cf4a908d9744b372ff2224bb291a320f36d76"
VALIDATOR_IMAGE = "gcr.io/etcd-development/etcd@sha256:a491baeaa0cb0c9cd89c0062ac44ece53886e3e5bddad18d2daf36678ce665b6"
ETCD_VERSION = "3.6.14"
NS = "flux-system"
SECRET = "rke2-etcd-restic"

# RKE2 consumes its existing credentials internally; neither they nor snapshot
# contents are returned in this metadata response.
CAPTURE = r'''
import hashlib,json,pathlib,subprocess,sys
prefix=sys.argv[1]
r=subprocess.run(['/usr/local/bin/rke2','etcd-snapshot','save','--name',prefix,
 '--etcd-s3=false','--etcd-snapshot-compress=false'],capture_output=True)
if r.returncode: sys.exit('Local snapshot creation failed')
files=list(pathlib.Path('/var/lib/rancher/rke2/server/db/snapshots').glob(prefix+'-*'))
if len(files)!=1 or not files[0].is_file() or files[0].is_symlink():
 sys.exit('Expected exactly one local snapshot')
p=files[0]
p.chmod(0o600)
size=p.stat().st_size
if not 0<size<=256*1024*1024: sys.exit('Snapshot exceeds the reviewed memory limit')
h=hashlib.file_digest(p.open('rb'),'sha256').hexdigest()
v=subprocess.check_output(['/usr/local/bin/rke2','--version'],text=True).splitlines()[0]
print(json.dumps({'path':str(p),'sha256':h,'size':size,'rke2_version':v}))
'''


def run(args, **kwargs):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            check=False, **kwargs)
    if result.returncode:
        # Backend errors can contain endpoint/credential details. Never echo them.
        raise RuntimeError(f"{args[0]} failed with exit code {result.returncode}")
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ssh-host', default='nas')
    parser.add_argument('--ssh-user', default='sofie')
    parser.add_argument('--identity-file')
    parser.add_argument('--host-key-alias', default='nas.home.finnes.dev')
    parser.add_argument('--initialize', action='store_true',
                        help='Explicitly initialize a new encrypted repository once')
    parser.add_argument('--local', action='store_true', help='Run on the RKE2 host as root')
    args = parser.parse_args()
    for value in (args.ssh_host, args.ssh_user, args.host_key_alias):
        if not re.fullmatch(r'[A-Za-z0-9_.:-]+', value) or value.startswith('-'):
            parser.error('Invalid SSH host, user or alias')
    ssh = ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
           '-o', 'ConnectTimeout=15', '-o', f'HostKeyAlias={args.host_key_alias}']
    if args.identity_file:
        ssh += ['-i', args.identity_file]
    ssh += [f'{args.ssh_user}@{args.ssh_host}']
    if not args.local:
        command = ['sudo', '-n', '/usr/local/libexec/rke2-restic-backup', '--local']
        if args.initialize:
            command.append('--initialize')
        sys.stdout.buffer.write(run(ssh + [shlex.join(command)]))
        return
    if os.geteuid() != 0:
        raise RuntimeError('Local backup requires root on the RKE2 host')
    os.environ['KUBECONFIG'] = '/etc/rancher/rke2/rke2.yaml'
    os.environ['PATH'] = '/var/lib/rancher/rke2/bin:/usr/local/bin:/usr/bin:/bin'
    # Every entry point shares the host lock, including manual and upgrade runs.
    lock = open('/var/lib/rke2-restic-backup/backup.lock', 'a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(
        RuntimeError('Backup service stopped before completion')))
    status_path = Path('/var/lib/rke2-restic-backup/status.json')
    metrics_path = Path('/var/lib/prometheus/node-exporter/rke2-etcd-restic.prom')
    status = json.loads(status_path.read_text()) if status_path.exists() else {}
    status.update(success=0, last_attempt=time.time())

    def persist():
        for path, content, mode in (
            (status_path, json.dumps(status, indent=2), 0o600),
            (metrics_path, '\n'.join([
                '# Only verified encrypted restores advance the success timestamp.',
                f"rke2_etcd_restic_backup_success {status['success']}",
                f"rke2_etcd_restic_backup_last_attempt_timestamp_seconds {status['last_attempt']}",
                f"rke2_etcd_restic_backup_last_verified_timestamp_seconds {status.get('last_verified', 0)}",
                f"rke2_etcd_restic_snapshot_count {status.get('snapshot_count', 0)}", '']), 0o644)):
            temporary = path.with_suffix('.tmp')
            temporary.write_text(content)
            temporary.chmod(mode)
            temporary.replace(path)
    persist()
    env = dict(os.environ, KUBECTL_REMOTE_COMMAND_WEBSOCKETS='false')
    kube = ['kubectl', '-n', NS]
    pod_name = 'etcd-restic-' + uuid.uuid4().hex[:12]
    prefix = 'encrypted-' + uuid.uuid4().hex[:12]

    def execute(*command, data=None):
        return run(kube + ['exec', '-i', pod_name, '-c', 'backup', '--', *command],
                   input=data, env=env)

    def state():
        return json.loads(run(kube + ['get', 'pod', pod_name, '-o', 'json']))

    def wait_for(predicate, seconds=300):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            value = state()
            if predicate(value):
                return value
            if value.get('status', {}).get('phase') == 'Failed':
                raise RuntimeError('Backup or native restore validation failed')
            time.sleep(2)
        raise RuntimeError('Timed out waiting for backup pod')

    version = run(['kubectl', '-n', 'kube-system', 'exec', 'etcd-nas', '--',
                   'etcd', '--version']).decode()
    if f'etcd Version: {ETCD_VERSION}\n' not in version:
        raise RuntimeError('Review and pin a validator matching the running etcd release')
    security = {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                'capabilities': {'drop': ['ALL']}}
    mounts = [{'name': 'work', 'mountPath': '/work'}]
    pod = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {
        'name': pod_name, 'namespace': NS,
        'labels': {'app.kubernetes.io/name': 'etcd-restic-verification'}}, 'spec': {
        'restartPolicy': 'Never', 'activeDeadlineSeconds': 1500,
        'automountServiceAccountToken': False,
        'securityContext': {'runAsNonRoot': True, 'runAsUser': 1000,
                            'runAsGroup': 1000, 'fsGroup': 1000,
                            'seccompProfile': {'type': 'RuntimeDefault'}},
        # The init container holds the in-memory transfer/restore directory until
        # the operator releases the native validator. No shell is needed in etcd.
        'initContainers': [{'name': 'backup', 'image': RESTIC_IMAGE,
            'command': ['/bin/sh', '-ec',
                        'umask 077; mkdir -p /work/source /work/tmp; '
                        'while [ ! -f /work/validate-ready ]; do sleep 1; done'],
            'envFrom': [{'secretRef': {'name': SECRET}}],
            'env': [{'name': 'HOME', 'value': '/work'},
                    {'name': 'TMPDIR', 'value': '/work/tmp'},
                    {'name': 'RESTIC_CACHE_DIR', 'value': '/work/cache'}],
            'securityContext': security, 'volumeMounts': mounts,
            'resources': {'requests': {'cpu': '100m', 'memory': '256Mi'},
                          'limits': {'memory': '2Gi'}}}],
        'containers': [{'name': 'validate', 'image': VALIDATOR_IMAGE,
            'command': ['/usr/local/bin/etcdutl', 'snapshot', 'status',
                        '/work/restored/work/source/snapshot.db', '--write-out=json'],
            'securityContext': security, 'volumeMounts': mounts,
            'resources': {'requests': {'cpu': '100m', 'memory': '128Mi'},
                          'limits': {'memory': '1Gi'}}}],
        'volumes': [{'name': 'work', 'emptyDir': {'medium': 'Memory',
                                               'sizeLimit': '1Gi'}}]}}
    # Recover cleanup after a host reboot or forced termination. The host lock
    # excludes another supported run; only terminal/expired labeled pods qualify.
    previous = json.loads(run(kube + ['get', 'pods', '-l',
        'app.kubernetes.io/name=etcd-restic-verification', '-o', 'json']))
    for item in previous['items']:
        created_at = datetime.datetime.fromisoformat(
            item['metadata']['creationTimestamp'].replace('Z', '+00:00')).timestamp()
        if item.get('status', {}).get('phase') in ('Succeeded', 'Failed') or time.time() - created_at > 1800:
            run(kube + ['delete', 'pod', item['metadata']['name'], '--wait=true', '--timeout=90s'])
    created = False
    try:
        run(kube + ['create', '-f', '-'], input=json.dumps(pod).encode())
        created = True
        wait_for(lambda d: any('running' in x.get('state', {}) for x in
                 d.get('status', {}).get('initContainerStatuses', [])))
        if args.initialize:
            execute('restic', 'init')
        # Failure here never causes an implicit init of a missing/wrong repository.
        execute('restic', 'cat', 'config')
        execute('/bin/sh', '-ec',
                'if RESTIC_PASSWORD=wrong-verification-password restic cat config '
                '>/dev/null 2>&1; then exit 1; fi')
        source = json.loads(run(['/usr/bin/python3', '-c', CAPTURE, prefix]))
        print('Local-only snapshot created; removed after verified encrypted restore.', flush=True)
        path = source['path']
        if not re.fullmatch('/var/lib/rancher/rke2/server/db/snapshots/'
                            + re.escape(prefix) + r'-[A-Za-z0-9_.-]+', path):
            raise RuntimeError('Unexpected source path')
        # Stream directly into the Kubernetes TLS connection without another disk copy.
        # A failed source must never be mistaken for a complete backup.
        sender = subprocess.Popen(['/usr/bin/cat', path],
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        receiver = subprocess.Popen(kube + ['exec', '-i', pod_name, '-c', 'backup',
            '--', '/bin/sh', '-ec', 'umask 077; cat > /work/source/snapshot.db'],
            stdin=sender.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=env)
        sender.stdout.close()
        try:
            received = receiver.wait(timeout=180)
            sent = sender.wait(timeout=30)
        finally:
            for process in (receiver, sender):
                if process.poll() is None:
                    process.kill()
                    process.wait()
        if sent or received:
            raise RuntimeError('Snapshot transfer failed; no backup attempted')
        digest = execute('sha256sum', '/work/source/snapshot.db').decode().split()[0]
        if digest != source['sha256']:
            raise RuntimeError('Transfer checksum mismatch; no backup attempted')
        source.update(etcd_version=ETCD_VERSION, validator_image=VALIDATOR_IMAGE,
                      restic_image=RESTIC_IMAGE,
                      created_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
        execute('/bin/sh', '-ec', 'umask 077; cat > /work/source/recovery.json',
                data=json.dumps(source).encode())
        records = execute('restic', 'backup', '--json', '--host', 'nas',
                          '--tag', 'rke2-etcd', '/work/source').decode().splitlines()
        summaries = [d for line in records if (d := json.loads(line)).get('message_type') == 'summary']
        snapshot = summaries[-1]['snapshot_id']
        if not re.fullmatch(r'[a-f0-9]{8,64}', snapshot):
            raise RuntimeError('Invalid restic snapshot ID')
        execute('restic', 'check', '--read-data')
        execute('restic', 'restore', snapshot, '--target', '/work/restored')
        restored = execute('sha256sum', '/work/restored/work/source/snapshot.db').decode().split()[0]
        if restored != source['sha256']:
            raise RuntimeError('Restored snapshot checksum mismatch')
        snapshots = json.loads(execute('restic', 'snapshots', '--json', '--tag', 'rke2-etcd'))
        execute('touch', '/work/validate-ready')
        wait_for(lambda d: d.get('status', {}).get('phase') == 'Succeeded')
        native = json.loads(run(kube + ['logs', pod_name, '-c', 'validate']))
        if native.get('totalKey', 0) <= 0 or native.get('revision', 0) <= 0:
            raise RuntimeError('Restored etcd database contains no usable state')
        receipt = {'repository_secret': SECRET, 'restic_snapshot': snapshot,
                   'source_sha256': source['sha256'], 'restored_sha256': restored,
                   'rke2_version': source['rke2_version'], 'etcd': native,
                   'verified_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
        # Persist recovery identity before unlinking the exact newly captured file.
        # Failed uploads/validation preserve the source; no old history is touched.
        status['receipt'] = receipt
        persist()
        Path(path).unlink()
        if Path(path).exists():
            raise RuntimeError('Verified snapshot plaintext cleanup failed')
        run(kube + ['delete', 'pod', pod_name, '--wait=true', '--timeout=90s'])
        created = False
        status.update(success=1, last_verified=time.time(), snapshot_count=len(snapshots))
        persist()
        print(json.dumps(receipt, indent=2))
    finally:
        if created:
            run(kube + ['delete', 'pod', pod_name, '--wait=true', '--timeout=90s'])


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, subprocess.TimeoutExpired, KeyError, ValueError, OSError) as exc:
        print(f'Encrypted etcd backup failed: {exc}', file=sys.stderr)
        sys.exit(1)
