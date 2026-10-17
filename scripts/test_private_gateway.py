# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
#
# SPDX-License-Identifier: MIT

"""Exercise the private Gateway acceptance playbook against controlled API responses."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
ANSIBLE = shutil.which('ansible-playbook')


@unittest.skipUnless(ANSIBLE, 'ansible-playbook is required')
class PrivateGatewayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='ops-private-gateway-test-')
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.service = {
            'metadata': {'labels': {'bgp': 'enabled'}},
            'spec': {
                'type': 'LoadBalancer',
                'loadBalancerClass': 'io.cilium/bgp-control-plane',
                'ipFamilies': ['IPv4'],
                'allocateLoadBalancerNodePorts': False,
                'ports': [{'port': 443}],
                'loadBalancerSourceRanges': [
                    '172.21.69.0/24', '172.21.70.0/24', '172.21.71.10/32',
                ],
            },
            'status': {'loadBalancer': {'ingress': [{'ip': '172.21.68.11'}]}},
        }
        parent = {'name': 'private', 'namespace': 'network', 'sectionName': 'https'}
        self.route = {
            'metadata': {'annotations': {
                'external-dns.kubernetes.io/target': '172.21.68.11',
                'external-dns.alpha.kubernetes.io/cloudflare-proxied': 'false',
            }},
            'spec': {
                'parentRefs': [parent], 'hostnames': ['jellyfin.home.finnes.dev'],
            },
            'status': {'parents': [{'parentRef': parent, 'conditions': [
                {'type': 'Accepted', 'status': 'True'},
                {'type': 'ResolvedRefs', 'status': 'True'},
            ]}]},
        }
        self.inventory = self.directory / 'hosts.yml'
        self.inventory.write_text(
            'all:\n  children:\n    rke2_servers:\n      hosts:\n        fixture:\n'
            '          ansible_connection: local\n          ansible_become: false\n'
        )
        self.kubectl = self.directory / 'kubectl'
        self.kubectl.write_text(
            '#!/usr/bin/env python3\nimport os, pathlib, sys\n'
            'root = pathlib.Path(os.environ["GATEWAY_FIXTURE"])\n'
            'if "service/cilium-gateway-private" in sys.argv:\n'
            '    print((root / "service.json").read_text())\n'
            'elif "httproute/jellyfin" in sys.argv:\n'
            '    print((root / "route.json").read_text())\n'
            'else:\n    print(os.environ["BGP_ADVERTISEMENTS"], end="")\n'
        )
        self.kubectl.chmod(0o755)
        curl = self.directory / 'curl'
        curl.write_text(
            '#!/usr/bin/env python3\nimport os, sys\n'
            'print(os.environ["PUBLIC_IPV6_STATUS" if any("[" in arg for arg in sys.argv) else "PUBLIC_STATUS"] if "--insecure" in sys.argv else "Healthy", end="")\n'
        )
        curl.chmod(0o755)
        self.environment = os.environ | {
            'PATH': str(self.directory) + ':' + os.environ['PATH'],
            'ANSIBLE_CONFIG': str(REPOSITORY / 'ansible/ansible.cfg'),
            'ANSIBLE_LOCAL_TEMP': str(self.directory / 'local'),
            'ANSIBLE_REMOTE_TEMP': str(self.directory / 'remote'),
            'ANSIBLE_VARS_ENABLED': 'host_group_vars',
            'ANSIBLE_NOCOLOR': '1',
            'GATEWAY_FIXTURE': str(self.directory),
            'PUBLIC_STATUS': '404',
            'PUBLIC_IPV6_STATUS': '404',
            'BGP_ADVERTISEMENTS': '172.21.68.11/32 172.21.68.10/32',
        }

    def run_playbook(self):
        (self.directory / 'service.json').write_text(json.dumps(self.service))
        (self.directory / 'route.json').write_text(json.dumps(self.route))
        result = subprocess.run(
            [ANSIBLE, '-i', str(self.inventory),
             str(REPOSITORY / 'ansible/playbooks/verify-private-gateway.yml'),
             '-e', json.dumps({'kubectl_argv': [str(self.kubectl)]})],
            env=self.environment, text=True, capture_output=True, timeout=45,
        )
        return result.returncode, result.stdout + result.stderr

    def test_private_endpoint_and_rejected_public_hostname_pass(self):
        code, output = self.run_playbook()
        self.assertEqual(code, 0, output)
        self.assertIn('changed=0', output)

    def test_unrestricted_service_is_rejected(self):
        self.service['spec']['loadBalancerSourceRanges'] = ['0.0.0.0/0']
        code, output = self.run_playbook()
        self.assertNotEqual(code, 0, output)
        self.assertIn('loadBalancerSourceRanges', output)

    def test_public_route_attachment_is_rejected(self):
        self.route['spec']['parentRefs'][0]['name'] = 'public'
        code, output = self.run_playbook()
        self.assertNotEqual(code, 0, output)
        self.assertIn('private_route.spec.parentRefs[0].name', output)

    def test_publicly_reachable_health_endpoint_is_rejected(self):
        self.environment['PUBLIC_STATUS'] = '200'
        code, output = self.run_playbook()
        self.assertNotEqual(code, 0, output)
        self.assertIn('Require both public Gateway addresses to reject', output)

    def test_public_ipv6_health_endpoint_is_rejected(self):
        self.environment['PUBLIC_IPV6_STATUS'] = '200'
        code, output = self.run_playbook()
        self.assertNotEqual(code, 0, output)
        self.assertIn('Require both public Gateway addresses to reject', output)

    def test_missing_private_bgp_advertisement_is_rejected(self):
        self.environment['BGP_ADVERTISEMENTS'] = '172.21.68.10/32'
        code, output = self.run_playbook()
        self.assertNotEqual(code, 0, output)
        self.assertIn('Require the private VIP and existing public VIP advertisements', output)


if __name__ == '__main__':
    unittest.main()
