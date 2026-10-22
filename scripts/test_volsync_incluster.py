# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT
"""Run the declared kubectl image with synthetic in-cluster TLS credentials."""
import http.server
import json
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.getenv('APPLICATION_RECOVERY_TEST') == '1', 'set APPLICATION_RECOVERY_TEST=1')
class InClusterClient(unittest.TestCase):
    def test_declared_refresher_uses_service_account_configuration(self):
        calls = []

        class API(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                if self.headers.get('Authorization') != 'Bearer synthetic-fixture-token':
                    self.send_error(401)
                    return
                path = self.path.split('?')[0]
                calls.append(path)
                if path == '/api':
                    value = {'kind': 'APIVersions', 'apiVersion': 'v1', 'versions': ['v1']}
                elif path == '/apis':
                    value = {'kind': 'APIGroupList', 'apiVersion': 'v1', 'groups': [
                        {'name': 'volsync.backube', 'versions': [{'groupVersion': 'volsync.backube/v1alpha1', 'version': 'v1alpha1'}],
                         'preferredVersion': {'groupVersion': 'volsync.backube/v1alpha1', 'version': 'v1alpha1'}}]}
                elif path in ['/api/v1', '/apis/volsync.backube/v1alpha1']:
                    value = {'kind': 'APIResourceList', 'apiVersion': 'v1', 'groupVersion': path.removeprefix('/apis/').removeprefix('/api/'),
                             'resources': [{'name': 'replicationsources', 'singularName': 'replicationsource', 'namespaced': True,
                                            'kind': 'ReplicationSource', 'verbs': ['get']}] if path.startswith('/apis/') else []}
                elif path == '/apis/volsync.backube/v1alpha1/namespaces/fixture/replicationsources/demo':
                    value = {'apiVersion': 'volsync.backube/v1alpha1', 'kind': 'ReplicationSource',
                             'metadata': {'name': 'demo', 'namespace': 'fixture'}, 'spec': {'paused': True}, 'status': {}}
                else:
                    self.send_error(404)
                    return
                payload = json.dumps(value).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        with tempfile.TemporaryDirectory(prefix='ops-incluster-fixture-') as directory:
            root = Path(directory)
            account = root / 'serviceaccount'
            account.mkdir(mode=0o755)
            (account / 'token').write_text('synthetic-fixture-token')
            (account / 'namespace').write_text('fixture')
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-subj', '/CN=127.0.0.1',
                            '-addext', 'subjectAltName=IP:127.0.0.1', '-keyout', str(root / 'key.pem'), '-out', str(account / 'ca.crt')],
                           check=True, capture_output=True, timeout=10)
            for file in account.iterdir():
                file.chmod(0o644)
            server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), API)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(account / 'ca.crt', root / 'key.pem')
            server.socket = context.wrap_socket(server.socket, server_side=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                image = subprocess.check_output(['yq', '-r', 'select(.kind == "CronJob") | .spec.jobTemplate.spec.template.spec.containers[0].image',
                                                 str(ROOT / 'kubernetes/components/volsync/refresh.yaml')], text=True).strip()
                subprocess.run(['docker', 'pull', image], check=True, capture_output=True, timeout=120)
                result = subprocess.run(['docker', 'run', '--rm', '--network=host', '--user=1001:1001', '--read-only', '--cap-drop=ALL',
                    '--security-opt=no-new-privileges', '--env', 'APP=demo', '--env', 'NS=fixture',
                    '--env', 'KUBERNETES_SERVICE_HOST=127.0.0.1', '--env', f'KUBERNETES_SERVICE_PORT={server.server_port}',
                    '--mount', f'type=bind,src={account},dst=/var/run/secrets/kubernetes.io/serviceaccount,readonly',
                    '--mount', f'type=bind,src={ROOT / "kubernetes/components/volsync/refresh.sh"},dst=/refresh.sh,readonly',
                    '--entrypoint', '/bin/bash', image, '/refresh.sh'], capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('/apis/volsync.backube/v1alpha1/namespaces/fixture/replicationsources/demo', calls)
                self.assertIn('no restore image requested', result.stdout)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
