# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
#
# SPDX-License-Identifier: 0BSD

"""Exercise the real bootstrap playbook without live services or credentials."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
ANSIBLE = shutil.which('ansible-playbook')


@unittest.skipUnless(ANSIBLE, 'ansible-playbook is required')
class BootstrapGuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='ops-bootstrap-test-')
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.inventory = self.directory / 'hosts.yml'
        self.inventory.write_text(
            'all:\n  children:\n    rke2_servers:\n      hosts:\n        fixture:\n'
            '          ansible_connection: local\n          ansible_become: false\n'
        )
        service = self.directory / 'systemctl'
        service.write_text('#!/bin/sh\nprintf "%s\\n" "$TEST_SERVICE_STATE" inactive\n')
        service.chmod(0o755)
        self.environment = os.environ | {
            'PATH': str(self.directory) + ':' + os.environ['PATH'],
            'ANSIBLE_CONFIG': str(REPOSITORY / 'ansible/ansible.cfg'),
            'ANSIBLE_LOCAL_TEMP': str(self.directory / 'local'),
            'ANSIBLE_REMOTE_TEMP': str(self.directory / 'remote'),
            'ANSIBLE_VARS_ENABLED': 'host_group_vars',
            'ANSIBLE_NOCOLOR': '1',
            'RKE2_RESTORE_SNAPSHOT': '',
            'RKE2_BOOTSTRAP_MODE': '',
        }
        self.playbook = REPOSITORY / 'ansible/playbooks/bootstrap-rke2-controlplane.yml'

    def run_playbook(self, state, *arguments):
        result = subprocess.run(
            [ANSIBLE, '-i', str(self.inventory), str(self.playbook), *arguments],
            env=self.environment | {'TEST_SERVICE_STATE': state},
            text=True, capture_output=True, timeout=45,
        )
        output = result.stdout + result.stderr
        self.assertIn('changed=0', output)
        self.assertNotIn('TASK [Load recovery credentials', output)
        self.assertNotIn('TASK [Clear abandoned', output)
        self.assertNotIn('TASK [rke2 :', output)
        return result.returncode, output

    def test_active_and_transitioning_services_are_untouched(self):
        for state in ('active', 'activating', 'deactivating', 'reloading'):
            with self.subTest(state=state):
                code, output = self.run_playbook(state)
                self.assertEqual(code, 0, output)
                self.assertNotIn('TASK [Gather facts only', output)

    def test_selecting_flux_tags_cannot_bypass_noop(self):
        code, output = self.run_playbook('active', '--tags', 'bootstrap-reconcile')
        self.assertEqual(code, 0, output)
        self.assertNotIn('TASK [Reconcile Flux', output)

    def test_stopped_existing_server_state_refuses_rebuild(self):
        # Substitute only the node's state path; run real setup/stat/assert tasks.
        server = self.directory / 'server'
        server.mkdir()
        (self.directory / 'tasks').mkdir()
        (self.directory / 'tasks/bootstrap-flux.yml').write_text(
            '- ansible.builtin.fail:\n    msg: Unexpected Flux execution\n'
        )
        source = self.playbook.read_text()
        self.playbook = self.directory / 'bootstrap.yml'
        self.playbook.write_text(source.replace(
            'path: /var/lib/rancher/rke2/server', 'path: ' + str(server)
        ))
        code, output = self.run_playbook(
            'inactive', '-e', 'ansible_distribution=Ubuntu',
            '-e', 'ansible_distribution_version=26.04',
            '-e', 'ansible_python_interpreter=/usr/bin/python3',
        )
        self.assertNotEqual(code, 0, output)
        self.assertIn('Stopped RKE2 server state still exists', output)
        self.assertTrue(server.is_dir())


if __name__ == '__main__':
    unittest.main()
