# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
#
# SPDX-License-Identifier: MIT

"""Exercise the Taskfile command with synthetic Kubernetes credentials."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ResticTaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = os.environ.copy()
        self.env.update(PATH=str(self.root) + os.pathsep + self.env['PATH'],
                        TEST_OUTPUT=str(self.root / 'output.json'),
                        TEST_CALLS=str(self.root / 'calls'),
                        RESTIC_REPOSITORY='s3:https://example.invalid/bucket/volsync-unrelated',
                        RESTIC_PASSWORD='inherited-password',
                        AWS_ACCESS_KEY_ID='inherited-id',
                        AWS_SECRET_ACCESS_KEY='inherited-key',
                        RESTIC_PASSWORD_FILE='inherited-password-file',
                        RESTIC_HOST='inherited-host')
        self.executable('kubectl', '''
import base64, os, sys
assert sys.argv[1:6] == ['-n', 'flux-system', 'get', 'secret', 'cluster-secrets']
with open(os.environ['TEST_CALLS'], 'a') as f:
    f.write(sys.argv[-1] + '\\n')
if os.environ.get('TEST_DENIED'):
    sys.exit(1)
fields = {'SECRET_S3_ENDPOINT': 's3:https://example.invalid/bucket/',
          'SECRET_PASSWORD': 'synthetic password',
          'SECRET_AWS_ACCESS_KEY_ID': 'synthetic-id',
          'SECRET_AWS_SECRET_ACCESS_KEY': 'synthetic-key'}
key = sys.argv[-1].removeprefix('jsonpath={.data.').removesuffix('}')
value = '' if os.environ.get('TEST_MISSING') == key else fields[key]
print(base64.b64encode(value.encode()).decode(), end='')
''')
        self.executable('restic', '''
import json, os, sys
with open(os.environ['TEST_OUTPUT'], 'w') as f:
    keys = ('RESTIC_REPOSITORY', 'RESTIC_PASSWORD', 'AWS_ACCESS_KEY_ID',
            'AWS_SECRET_ACCESS_KEY', 'RESTIC_PASSWORD_FILE', 'RESTIC_HOST')
    json.dump({'args': sys.argv[1:],
               'env': {key: os.environ[key] for key in keys if key in os.environ}}, f)
sys.exit(int(os.environ.get('TEST_RESTIC_STATUS', '0')))
''')

    def executable(self, name, source):
        path = self.root / name
        path.write_text('#!/usr/bin/env python3\n' + source)
        path.chmod(0o755)

    def run_task(self, *args):
        return subprocess.run(['task', 'volsync:restic', *args], cwd=ROOT,
                              env=self.env, capture_output=True, text=True)

    def test_shared_credentials_and_arguments(self):
        result = self.run_task('APP=jellyfin-media', '--', 'snapshots', '--path', '/a b')
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads((self.root / 'output.json').read_text())
        self.assertEqual(output['args'], ['snapshots', '--path', '/a b'])
        self.assertEqual(output['env']['RESTIC_REPOSITORY'],
                         's3:https://example.invalid/bucket/volsync-jellyfin-media')
        self.assertEqual(output['env']['RESTIC_PASSWORD'], 'synthetic password')
        self.assertEqual(output['env']['AWS_ACCESS_KEY_ID'], 'synthetic-id')
        self.assertEqual(output['env']['AWS_SECRET_ACCESS_KEY'], 'synthetic-key')
        self.assertNotIn('RESTIC_PASSWORD_FILE', output['env'])
        self.assertNotIn('RESTIC_HOST', output['env'])
        self.assertNotIn('synthetic', result.stdout + result.stderr)

    def test_missing_or_denied_credentials_stop_restic(self):
        for setting in ({'TEST_DENIED': '1'}, {'TEST_MISSING': 'SECRET_PASSWORD'}):
            with self.subTest(setting=setting):
                self.env.update(setting)
                result = self.run_task('APP=jellyfin', '--', 'snapshots')
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / 'output.json').exists())
                for key in setting:
                    self.env.pop(key)

    def test_unrelated_task_listing_does_not_fetch_credentials(self):
        result = subprocess.run(['task', '--list'], cwd=ROOT, env=self.env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / 'calls').exists())

    def test_restic_failure_is_reported(self):
        self.env['TEST_RESTIC_STATUS'] = '7'
        self.assertNotEqual(self.run_task('APP=openldap', '--', 'check').returncode, 0)

    def test_invalid_or_missing_app_does_not_fetch_credentials(self):
        for args in (('--', 'snapshots'), ('APP=../openldap', '--', 'snapshots')):
            with self.subTest(args=args):
                self.assertNotEqual(self.run_task(*args).returncode, 0)
                self.assertFalse((self.root / 'calls').exists())


if __name__ == '__main__':
    unittest.main()
