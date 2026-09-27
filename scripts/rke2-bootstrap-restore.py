#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
#
# SPDX-License-Identifier: 0BSD

"""Prepare a verified encrypted etcd backup for an explicit RKE2 bootstrap reset.

Supply restic/S3 credentials through the environment. This helper neither starts
RKE2 nor resets etcd and refuses to run when a live etcd data directory exists.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

LIVE_ETCD = Path('/var/lib/rancher/rke2/server/db/etcd')
DEFAULT_TARGET = Path('/var/lib/rancher/rke2/server/db/snapshots/bootstrap')
MAX_SNAPSHOT_BYTES = 256 * 1024 * 1024
ETCD_VERSION = '3.6.14'
VERSION = re.compile(r'v(\d+)\.(\d+)\.(\d+)\+rke2r(\d+)')


class RestoreError(Exception):
    """Fixed diagnostics that cannot disclose credentials or snapshot contents."""


def run(argv):
    result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=1200, check=False)
    if result.returncode:
        raise RestoreError('A restore command failed; sensitive diagnostics suppressed')
    return result.stdout


def ensure_no_live_etcd(etcd_dir):
    if os.path.lexists(etcd_dir):
        raise RestoreError('Existing etcd data directory prevents bootstrap restore')


def compatible_version(source, target):
    source_match = re.fullmatch(r'(?:rke2 version )?(v\d+\.\d+\.\d+\+rke2r\d+)(?: \([^\n]*\))?', source)
    target_match = VERSION.fullmatch(target)
    if not source_match or not target_match:
        raise RestoreError('Unrecognized source or target RKE2 version')
    source_version = source_match.group(1)
    previous = tuple(map(int, VERSION.fullmatch(source_version).groups()))
    desired = tuple(map(int, target_match.groups()))
    if desired < previous or desired[0] != previous[0] or desired[1] > previous[1] + 1:
        raise RestoreError('RKE2 restore would downgrade or skip a minor release')
    return source_version



def dataset_guid(dataset):
    return run(['/usr/sbin/zfs', 'get', '-H', '-p', '-o', 'value', 'guid', dataset]).decode().strip()


def validate_volumes(metadata):
    inventory = metadata.get('volume_inventory')
    if not isinstance(inventory, list) or not inventory:
        raise RestoreError('Snapshot has no volume inventory; use clean GitOps recovery')
    claims, datasets = set(), set()
    for volume in inventory:
        if not isinstance(volume, dict) or set(volume) != {'namespace', 'pvc', 'dataset', 'guid'}:
            raise RestoreError('Invalid recovery volume inventory')
        namespace, pvc, dataset, guid = (volume[k] for k in ('namespace', 'pvc', 'dataset', 'guid'))
        if (not all(isinstance(value, str) for value in (namespace, pvc, dataset, guid))
                or not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', namespace)
                or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', pvc)
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]*(?:/[A-Za-z0-9][A-Za-z0-9_.:-]*)+', dataset)
                or not re.fullmatch(r'[1-9][0-9]{0,19}', guid)
                or int(guid) >= 2 ** 64):
            raise RestoreError('Invalid recovery volume identity')
        if (namespace, pvc) in claims or dataset in datasets:
            raise RestoreError('Duplicate recovery volume identity')
        claims.add((namespace, pvc))
        datasets.add(dataset)
    for volume in inventory:
        try:
            current = dataset_guid(volume['dataset'])
        except RestoreError:
            raise RestoreError('Required dataset is unavailable; use clean GitOps recovery') from None
        if current != volume['guid']:
            raise RestoreError('Dataset identity changed; use clean GitOps recovery')
    return len(inventory)


def select_snapshot(requested, restic):
    if requested != 'latest' and not re.fullmatch('[a-f0-9]{8,64}', requested):
        raise RestoreError('Snapshot must be latest or an exact hexadecimal restic ID')
    snapshots = json.loads(run([restic, 'snapshots', '--json', '--host', 'nas',
                               '--tag', 'rke2-etcd', '--path', '/work/source']))
    eligible = [s for s in snapshots if s.get('hostname') == 'nas'
                and s.get('paths') == ['/work/source'] and 'rke2-etcd' in s.get('tags', [])
                and re.fullmatch('[a-f0-9]{64}', s.get('id', ''))]
    if requested != 'latest':
        eligible = [s for s in eligible if s['id'].startswith(requested)]
        if len(eligible) != 1:
            raise RestoreError('Requested snapshot is missing, ambiguous, or not a normalized backup')
    if not eligible:
        raise RestoreError('No normalized encrypted etcd snapshot is available')
    selected = max(eligible, key=lambda s: datetime.datetime.fromisoformat(s['time'].replace('Z', '+00:00')))
    return selected['id']


def restore(requested, target, etcdutl, target_version, *, etcd_dir=LIVE_ETCD, restic='restic'):
    # The injectable paths are a Python test seam only. Production CLI always
    # checks the real etcd data directory, before even contacting the repository.
    ensure_no_live_etcd(etcd_dir)
    target = Path(target)
    if not target.is_absolute() or target != target.resolve():
        raise RestoreError('Restore target must be an absolute path without symlink components')
    if os.path.lexists(target):
        raise RestoreError('Restore target already exists; preserve the previous recovery artifact')
    if not VERSION.fullmatch(target_version):
        raise RestoreError('Unrecognized target RKE2 version')
    if f'etcdutl version: {ETCD_VERSION}' not in run([etcdutl, 'version']).decode():
        raise RestoreError('The reviewed etcdutl validator is required')
    snapshot = select_snapshot(requested, restic)
    ensure_no_live_etcd(etcd_dir)
    # Stage beside the destination so the final rename is atomic even when the
    # database directory is a separate filesystem. umask and chmod protect data.
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.rke2-bootstrap-', dir=target.parent) as temporary:
        staging = Path(temporary)
        staging.chmod(0o700)
        run([restic, 'restore', snapshot, '--target', str(staging),
             '--include', '/work/source/snapshot.db', '--include', '/work/source/recovery.json'])
        source = staging / 'work/source'
        database, metadata_file = source / 'snapshot.db', source / 'recovery.json'
        for path in (source, database, metadata_file):
            if path.is_symlink() or path.resolve() != path:
                raise RestoreError('Restored paths must not contain symlinks')
        if not database.is_file() or not 0 < database.stat().st_size <= MAX_SNAPSHOT_BYTES:
            raise RestoreError('Restored database is missing or exceeds the reviewed size bound')
        if not metadata_file.is_file() or metadata_file.stat().st_size > 1024 * 1024:
            raise RestoreError('Recovery metadata is missing or oversized')
        metadata = json.loads(metadata_file.read_bytes())
        expected = metadata.get('sha256', '')
        if not isinstance(expected, str) or not re.fullmatch('[a-f0-9]{64}', expected):
            raise RestoreError('Recovery metadata has no valid snapshot checksum')
        with database.open('rb') as data:
            actual = hashlib.file_digest(data, 'sha256').hexdigest()
        if actual != expected or database.stat().st_size != metadata.get('size'):
            raise RestoreError('Restored snapshot does not match its recovery metadata')
        source_version = compatible_version(metadata.get('rke2_version', ''), target_version)
        if metadata.get('etcd_version') != ETCD_VERSION:
            raise RestoreError('Source snapshot requires a different native validator')
        volume_count = validate_volumes(metadata)
        native = json.loads(run([etcdutl, 'snapshot', 'status', str(database), '--write-out=json']))
        if native.get('totalKey', 0) <= 0 or native.get('revision', 0) <= 0:
            raise RestoreError('Restored etcd snapshot contains no usable state')
        ensure_no_live_etcd(etcd_dir)
        # Publish only the validated database; do not leave recovery metadata or
        # unknown restored files alongside the bootstrap input.
        publish = staging / 'verified'
        publish.mkdir(mode=0o700)
        database.chmod(0o600)
        database.replace(publish / 'snapshot.db')
        if os.path.lexists(target):
            raise RestoreError('Restore target appeared during verification')
        publish.rename(target)
    return {'restic_snapshot': snapshot, 'source_rke2_version': source_version,
            'target_rke2_version': target_version, 'sha256': actual,
            'etcd': {key: native[key] for key in ('hash', 'revision', 'totalKey', 'totalSize') if key in native},
            'snapshot_path': str(target / 'snapshot.db'), 'verified_volume_count': volume_count}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', default='latest')
    parser.add_argument('--target', type=Path, default=DEFAULT_TARGET)
    parser.add_argument('--etcdutl', required=True)
    parser.add_argument('--rke2-version', required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RestoreError('Bootstrap restore requires root on the RKE2 host')
    os.umask(0o077)
    def interrupted(*_):
        raise RestoreError('Restore interrupted before completion')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    print(json.dumps(restore(args.snapshot, args.target, args.etcdutl, args.rke2_version)))


if __name__ == '__main__':
    try:
        main()
    except RestoreError as exc:
        print(f'Encrypted bootstrap restore failed: {exc}', file=sys.stderr)
        sys.exit(1)
    except Exception:
        print('Encrypted bootstrap restore failed; sensitive diagnostics suppressed.', file=sys.stderr)
        sys.exit(1)
