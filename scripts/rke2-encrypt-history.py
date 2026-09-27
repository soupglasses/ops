#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
#
# SPDX-License-Identifier: 0BSD

"""Migrate native S3 etcd history before separately deleting verified originals.

Run on the RKE2 host as root. Credentials remain inside this process; all source
bytes and native validation files live in memory-backed storage. No source is
removed by the default migration stage. The delete stage requires the unchanged
inventory and its independently restored encrypted coverage index.
"""

import argparse
import datetime
import fcntl
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path

import boto3
import yaml
from botocore.config import Config
from botocore.exceptions import ClientError

RESTIC_IMAGE = 'quay.io/backube/volsync@sha256:0d03a6aad57569eba2c0eaa0848cf4a908d9744b372ff2224bb291a320f36d76'
LIMIT = 256 * 1024 * 1024
STATE = Path('/var/lib/rke2-restic-backup/history-coverage.json')


class MigrationError(Exception):
    """Fixed operator-safe errors raised only by this helper."""


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def run(argv, **kwargs):
    result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=1200, check=False, **kwargs)
    if result.returncode:
        raise MigrationError('A required command failed; sensitive diagnostics suppressed')
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--migrate', action='store_true')
    mode.add_argument('--delete', action='store_true')
    parser.add_argument('--etcdutl', required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise MigrationError('Run on the RKE2 host as root')
    os.umask(0o077)
    os.environ.update(KUBECONFIG='/etc/rancher/rke2/rke2.yaml',
                      PATH='/var/lib/rancher/rke2/bin:/usr/local/bin:/usr/bin:/bin',
                      KUBECTL_REMOTE_COMMAND_WEBSOCKETS='false')
    STATE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    STATE.parent.chmod(0o700)
    lock = (STATE.parent / 'backup.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if b'etcdutl version: 3.6.14' not in run([args.etcdutl, 'version']):
        raise MigrationError('The reviewed etcdutl 3.6.14 validator is required')
    if run(['findmnt', '-n', '-o', 'FSTYPE', '-T', '/run']).strip() != b'tmpfs':
        raise MigrationError('Native restore validation requires /run to be tmpfs')
    # Consume existing runtime configuration internally, without exporting it to
    # a file, a subprocess environment, command arguments, or operator output.
    with open('/etc/rancher/rke2/config.yaml') as config_file:
        config = yaml.safe_load(config_file)
    endpoint = config['etcd-s3-endpoint']
    if config.get('etcd-s3-insecure') or config.get('etcd-s3-skip-ssl-verify'):
        raise MigrationError('Source endpoint must use verified TLS')
    if '://' not in endpoint:
        endpoint = 'https://' + endpoint
    if not endpoint.startswith('https://'):
        raise MigrationError('Source endpoint must use HTTPS')
    bucket = config['etcd-s3-bucket']
    folder = config.get('etcd-s3-folder', '').strip('/')
    if not folder or any(part in ('.', '..') for part in folder.split('/')):
        raise MigrationError('A nonempty, explicit source folder is required')
    prefix = folder + '/'
    client = boto3.client('s3', endpoint_url=endpoint,
        region_name=config.get('etcd-s3-region', 'us-east-1'),
        aws_access_key_id=config['etcd-s3-access-key'],
        aws_secret_access_key=config['etcd-s3-secret-key'],
        aws_session_token=config.get('etcd-s3-session-token'),
        verify=config.get('etcd-s3-endpoint-ca') or True,
        config=Config(signature_version='s3v4', connect_timeout=15, read_timeout=120,
                      retries={'max_attempts': 3}, s3={'addressing_style': 'path'}))
    del config

    def validate_key(key):
        relative = key[len(prefix):]
        base = relative.removeprefix('.metadata/')
        if not key.startswith(prefix) or '/' in base or not re.fullmatch(
                r'[A-Za-z0-9_.-]+-[0-9]+(?:\.zip)?', base):
            raise MigrationError('Unrecognized source object; scope requires review')
        return relative

    def inventory():
        objects, markers = [], []
        latest = {}
        for page in client.get_paginator('list_multipart_uploads').paginate(
                Bucket=bucket, Prefix=prefix):
            if page.get('Uploads'):
                raise MigrationError('Source multipart uploads remain; no parts will be deleted')
        # Never fall back to current objects if version listing fails: hidden
        # historical versions could otherwise remain as exposed plaintext.
        for page in client.get_paginator('list_object_versions').paginate(
                Bucket=bucket, Prefix=prefix):
            for value in page.get('Versions', []):
                key = value['Key']
                relative = validate_key(key)
                size = value['Size']
                if not 0 < size <= LIMIT:
                    raise MigrationError('Source object exceeds the reviewed size bound')
                if value['IsLatest']:
                    if key in latest:
                        raise MigrationError('Duplicate latest source version')
                    latest[key] = (value['ETag'], size)
                objects.append({'key': key, 'version': value['VersionId'],
                                'etag': value['ETag'], 'size': size,
                                'modified': value['LastModified'].isoformat(),
                                'metadata': relative.startswith('.metadata/')})
            for value in page.get('DeleteMarkers', []):
                validate_key(value['Key'])
                markers.append({'key': value['Key'], 'version': value['VersionId']})
        current = {}
        for page in client.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix=prefix):
            for value in page.get('Contents', []):
                if value['Key'] in current:
                    raise MigrationError('Duplicate current source object')
                current[value['Key']] = (value['ETag'], value['Size'])
        if current != latest:
            raise MigrationError('Current objects differ from the latest version inventory')
        snapshots = {o['key'][len(prefix):] for o in objects if not o['metadata']}
        if any(o['key'][len(prefix):].removeprefix('.metadata/') not in snapshots
               for o in objects if o['metadata']):
            raise MigrationError('Orphan snapshot metadata requires explicit review')
        if any(not m['key'].startswith(prefix) for m in markers):
            raise MigrationError('Out-of-scope delete marker')
        return sorted(objects, key=lambda v: (v['key'], v['version'])), sorted(
            markers, key=lambda v: (v['key'], v['version']))

    objects, markers = inventory()
    fingerprint = digest({'objects': objects, 'markers': markers,
                          'endpoint': endpoint, 'bucket': bucket, 'prefix': prefix})
    if not objects:
        raise MigrationError('No source objects found; no changes made')
    kube = ['kubectl', '-n', 'flux-system']
    pod_name = 'etcd-history-' + uuid.uuid4().hex[:12]
    pod = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': pod_name,
        'namespace': 'flux-system', 'labels': {'app.kubernetes.io/name': 'etcd-history-migration'}},
        'spec': {'restartPolicy': 'Never', 'activeDeadlineSeconds': 3600,
            'automountServiceAccountToken': False,
            'securityContext': {'runAsNonRoot': True, 'runAsUser': 1000,
                'runAsGroup': 1000, 'fsGroup': 1000,
                'seccompProfile': {'type': 'RuntimeDefault'}},
            'containers': [{'name': 'backup', 'image': RESTIC_IMAGE,
                'command': ['/bin/sh', '-ec', 'umask 077; mkdir -p /work/tmp; sleep 3600'],
                'envFrom': [{'secretRef': {'name': 'rke2-etcd-restic'}}],
                'env': [{'name': 'HOME', 'value': '/work'},
                    {'name': 'TMPDIR', 'value': '/work/tmp'},
                    {'name': 'RESTIC_CACHE_DIR', 'value': '/work/cache'}],
                'securityContext': {'allowPrivilegeEscalation': False,
                    'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}},
                'resources': {'requests': {'cpu': '100m', 'memory': '256Mi'},
                              'limits': {'memory': '2Gi'}},
                'volumeMounts': [{'name': 'work', 'mountPath': '/work'}]}],
            'volumes': [{'name': 'work', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '1Gi'}}]}}

    def command(*argv):
        return kube + ['exec', '-i', pod_name, '-c', 'backup', '--', *argv]

    def execute(*argv, data=None):
        return run(command(*argv), input=data)

    def backup(path, tag):
        summaries = [v for line in execute('restic', 'backup', '--json', '--host', 'nas',
            '--tag', 'rke2-etcd-history', '--tag', tag, path).splitlines()
            if (v := json.loads(line)).get('message_type') == 'summary']
        snapshot = summaries[-1]['snapshot_id']
        if not re.fullmatch('[a-f0-9]{8,64}', snapshot):
            raise MigrationError('Invalid restic snapshot identity')
        return snapshot

    def native_validate(path, compressed):
        db = path
        if compressed:
            with zipfile.ZipFile(path) as archive:
                members = archive.infolist()
                if len(members) != 1:
                    raise MigrationError('Expected exactly one ZIP snapshot member')
                member = members[0]
                unix_mode = member.external_attr >> 16
                if (member.is_dir() or '/' in member.filename or '\\' in member.filename
                        or member.filename in ('.', '..') or stat.S_ISLNK(unix_mode)
                        or not 0 < member.file_size <= LIMIT):
                    raise MigrationError('Unsafe or oversized compressed snapshot')
                db = path.with_name('snapshot.db')
                with archive.open(member) as source, db.open('wb') as target:
                    copied = 0
                    while chunk := source.read(1024 * 1024):
                        copied += len(chunk)
                        if copied > LIMIT:
                            raise MigrationError('Expanded snapshot exceeds size bound')
                        target.write(chunk)
                if copied != member.file_size:
                    raise MigrationError('ZIP member length mismatch')
        native = json.loads(run([args.etcdutl, 'snapshot', 'status', str(db), '--write-out=json']))
        if native.get('totalKey', 0) <= 0 or native.get('revision', 0) <= 0:
            raise MigrationError('Native etcd validation found no usable state')
        return native

    def verify(snapshot, obj, expected):
        execute('rm', '-rf', '/work/restored')
        execute('restic', 'restore', snapshot, '--target', '/work/restored')
        restored = '/work/restored/work/source/original'
        if execute('sha256sum', restored).decode().split()[0] != expected:
            raise MigrationError('Restored object checksum mismatch')
        if obj['metadata']:
            return None
        # The host-side validator has no S3 credentials in its environment. Only
        # the restored snapshot crosses this pipe, into root-only tmpfs storage.
        with tempfile.TemporaryDirectory(prefix='rke2-etcd-history-', dir='/run') as directory:
            path = Path(directory) / 'original'
            process = subprocess.Popen(command('cat', restored), stdout=subprocess.PIPE,
                                       stderr=subprocess.DEVNULL)
            try:
                total = 0
                with path.open('wb') as target:
                    while chunk := process.stdout.read(1024 * 1024):
                        total += len(chunk)
                        if total > LIMIT:
                            raise MigrationError('Restored object exceeds size bound')
                        target.write(chunk)
                if process.wait(timeout=120) or total != obj['size']:
                    raise MigrationError('Native validator transfer failed')
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                process.stdout.close()
            with path.open('rb') as source:
                if hashlib.file_digest(source, 'sha256').hexdigest() != expected:
                    raise MigrationError('Native validator transfer checksum mismatch')
            return native_validate(path, obj['key'].endswith('.zip'))

    def deadline(*_):
        raise MigrationError('Migration deadline or termination signal reached')

    signal.signal(signal.SIGALRM, deadline)
    signal.signal(signal.SIGTERM, deadline)
    signal.alarm(3500)
    created = False
    try:
        run(kube + ['create', '-f', '-'], input=encoded(pod))
        created = True
        run(kube + ['wait', '--for=condition=Ready', 'pod/' + pod_name, '--timeout=180s'])
        execute('restic', 'cat', 'config')
        execute('/bin/sh', '-ec', 'if RESTIC_PASSWORD=wrong-verification-password '
                'restic cat config >/dev/null 2>&1; then exit 1; fi')
        if args.delete:
            coverage = json.loads(STATE.read_text())
            if coverage['inventory_sha256'] != fingerprint:
                raise MigrationError('Source inventory changed; repeat migration before deleting')
            index_id = coverage.pop('index_snapshot')
            index = json.loads(execute('restic', 'dump', index_id, '/work/index/coverage.json'))
            if index != coverage:
                raise MigrationError('Encrypted coverage index does not match local receipts')
            execute('restic', 'check', '--read-data')
            expected_ids = {digest(o) for o in objects}
            if set(coverage['receipts']) != expected_ids:
                raise MigrationError('Coverage index omits source objects')
            # Re-restore every object before authorizing any source deletion.
            for obj in objects:
                receipt = coverage['receipts'][digest(obj)]
                verify(receipt['snapshot'], obj, receipt['sha256'])
            if inventory() != (objects, markers):
                raise MigrationError('Source inventory changed during verification')
            for obj in sorted(objects, key=lambda o: not o['metadata']):
                parameters = {'Bucket': bucket, 'Key': obj['key'], 'VersionId': obj['version']}
                # A null version can be overwritten; fail closed when the server
                # does not support conditional deletion rather than deleting new data.
                if obj['version'] == 'null':
                    parameters['IfMatch'] = obj['etag']
                client.delete_object(**parameters)
            for marker in markers:
                client.delete_object(Bucket=bucket, Key=marker['key'], VersionId=marker['version'])
            remaining, remaining_markers = inventory()
            if remaining or remaining_markers:
                raise MigrationError('Source versions remain; deletion is not complete')
            print(json.dumps({'deleted_verified_objects': len(objects),
                              'deleted_markers': len(markers), 'index_snapshot': index_id}))
            return
        coverage = {'inventory_sha256': fingerprint, 'receipts': {},
                    'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    'validator': 'etcdutl 3.6.14', 'restic_image': RESTIC_IMAGE}
        for number, obj in enumerate(objects, 1):
            execute('rm', '-rf', '/work/source', '/work/restored')
            execute('mkdir', '-p', '/work/source')
            response = client.get_object(Bucket=bucket, Key=obj['key'],
                VersionId=obj['version'], IfMatch=obj['etag'])
            receiver = subprocess.Popen(command('/bin/sh', '-ec',
                'umask 077; cat > /work/source/original'), stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            hash_value, total = hashlib.sha256(), 0
            try:
                for chunk in response['Body'].iter_chunks(chunk_size=1024 * 1024):
                    total += len(chunk)
                    if total > LIMIT:
                        raise MigrationError('Downloaded object exceeds size bound')
                    hash_value.update(chunk)
                    receiver.stdin.write(chunk)
                receiver.stdin.close()
                if receiver.wait(timeout=120) or total != obj['size']:
                    raise MigrationError('Source transfer incomplete')
            finally:
                response['Body'].close()
                if receiver.poll() is None:
                    receiver.kill()
                    receiver.wait()
            sha256 = hash_value.hexdigest()
            if execute('sha256sum', '/work/source/original').decode().split()[0] != sha256:
                raise MigrationError('Source transfer checksum mismatch')
            metadata = dict(obj, sha256=sha256, user_metadata=response.get('Metadata', {}),
                            bucket=bucket, endpoint=endpoint)
            execute('/bin/sh', '-ec', 'umask 077; cat > /work/source/recovery.json',
                    data=encoded(metadata))
            identity = digest(obj)
            snapshot = backup('/work/source', 'object-' + identity)
            native = verify(snapshot, obj, sha256)
            coverage['receipts'][identity] = {'snapshot': snapshot, 'sha256': sha256,
                                              'size': total, 'native': native}
            print(f'Encrypted and restore-verified object {number}/{len(objects)}', flush=True)
        execute('restic', 'check', '--read-data')
        execute('mkdir', '-p', '/work/index')
        execute('/bin/sh', '-ec', 'umask 077; cat > /work/index/coverage.json', data=encoded(coverage))
        index_id = backup('/work/index', 'coverage-' + fingerprint)
        if json.loads(execute('restic', 'dump', index_id, '/work/index/coverage.json')) != coverage:
            raise MigrationError('Encrypted coverage index restore mismatch')
        coverage['index_snapshot'] = index_id
        temporary = STATE.with_suffix('.tmp')
        temporary.write_bytes(encoded(coverage))
        temporary.replace(STATE)
        print(json.dumps({'verified_objects': len(objects), 'index_snapshot': index_id,
                          'source_untouched': True}))
    finally:
        signal.alarm(0)
        if created:
            run(kube + ['delete', 'pod', pod_name, '--wait=true', '--timeout=90s'])


if __name__ == '__main__':
    try:
        main()
    except MigrationError as exc:
        print(f'Historical backup migration failed: {exc}', file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        # Never print external exception messages: SDK/YAML diagnostics can
        # contain credentials, object metadata, or response body excerpts.
        kind = type(exc).__name__
        kind = kind if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,79}', kind) else 'ExternalError'
        detail = kind
        if isinstance(exc, ClientError):
            code = exc.response.get('Error', {}).get('Code', '')
            allowed = {'AccessDenied', 'InvalidRequest', 'NotImplemented', 'NoSuchBucket',
                       'SignatureDoesNotMatch', 'PreconditionFailed', 'MethodNotAllowed',
                       'InvalidArgument', 'RequestTimeout', 'InternalError', 'ServiceUnavailable',
                       'SlowDown', 'NoSuchKey', 'NoSuchVersion', 'InvalidAccessKeyId', 'ExpiredToken'}
            if isinstance(code, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9]{0,63}', code) and code in allowed:
                detail += ':' + code
        print(f'Historical backup migration failed ({detail}); sensitive diagnostics suppressed.',
              file=sys.stderr)
        sys.exit(1)
