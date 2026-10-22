# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT
"""Validate backup and one-shot recovery wiring after Flux substitution."""
import json
from pathlib import Path
import subprocess
import sys


def verify(path):
    output = subprocess.run(['yq', 'eval-all', '-o=json', '-I=0', '.', str(path)],
                            capture_output=True, text=True, check=True).stdout
    objects = [json.loads(line) for line in output.splitlines() if line.strip()]
    objects = [obj for obj in objects if isinstance(obj, dict)]
    index = {(obj.get('kind'), obj['metadata'].get('namespace'), obj['metadata']['name']): obj for obj in objects}
    sources = {(obj['metadata']['namespace'], obj['metadata']['name']) for obj in objects
               if obj.get('kind') == 'ReplicationSource' and 'restic' in obj.get('spec', {})}
    for namespace, app in sources:
        source = index['ReplicationSource', namespace, app]['spec']
        destination = index['ReplicationDestination', namespace, app + '-dst']['spec']
        claim = index['PersistentVolumeClaim', namespace, app]['spec']
        assert source['trigger']['schedule']
        assert source['restic']['retain']
        assert source['restic']['pruneIntervalDays'] > 0
        assert destination['trigger'] == {'manual': 'restore-once'}
        assert destination['restic']['repository'] == source['restic']['repository']
        assert 'destinationPVC' not in destination['restic']
        assert claim['dataSourceRef'] == {
            'kind': 'ReplicationDestination', 'apiGroup': 'volsync.backube', 'name': app + '-dst'}
        for suffix in ['-restore-refresh', '-backup-check']:
            assert ('CronJob', namespace, app + suffix) not in index, 'Scheduled backup pulls are forbidden'
    print('VolSync push/prune and one-shot recovery contracts passed')


if __name__ == '__main__':
    verify(Path(sys.argv[1]))
