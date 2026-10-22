# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT
"""Validate backup refresher wiring after Flux substitution."""
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
    expected_script = (Path(__file__).resolve().parents[1] / 'kubernetes/components/volsync/refresh.sh').read_text()
    sources = {(obj['metadata']['namespace'], obj['metadata']['name']) for obj in objects
               if obj.get('kind') == 'ReplicationSource' and 'restic' in obj.get('spec', {})}
    for namespace, app in sources:
        name = app + '-restore-refresh'
        cron = index['CronJob', namespace, name]['spec']
        assert cron['concurrencyPolicy'] == 'Forbid'
        pod = cron['jobTemplate']['spec']['template']['spec']
        assert pod['serviceAccountName'] == name
        container = pod['containers'][0]
        assert next(env for env in container['env'] if env['name'] == 'APP')['value'] == app
        assert next(env for env in container['env'] if env['name'] == 'NS')['valueFrom']['fieldRef']['fieldPath'] == 'metadata.namespace'
        config = index['ConfigMap', namespace, pod['volumes'][0]['configMap']['name']]
        assert config['data']['refresh.sh'] == expected_script, 'Flux changed runtime shell variables'
        role = index['Role', namespace, name]
        assert role['rules'] == [
            {'apiGroups': ['volsync.backube'], 'resources': ['replicationsources'], 'resourceNames': [app], 'verbs': ['get']},
            {'apiGroups': ['volsync.backube'], 'resources': ['replicationdestinations'], 'resourceNames': [app + '-dst'], 'verbs': ['get', 'patch']}]
    for obj in objects:
        if obj.get('kind') == 'CronJob' and obj['metadata']['name'].endswith('-restore-refresh'):
            assert (obj['metadata']['namespace'], obj['metadata']['name'].removesuffix('-restore-refresh')) in sources
    print('VolSync refresher source ownership, RBAC and runtime shell contracts passed')


if __name__ == '__main__':
    verify(Path(sys.argv[1]))
