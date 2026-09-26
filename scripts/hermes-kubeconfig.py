#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT

"""Rotate bounded Hermes ServiceAccount credentials through verified SSH connections."""

import argparse
import os
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path

PROFILES = ("opslead", "incident", "observability", "implementer", "security", "resilience")

# Run on nas. Only the resulting credential envelope goes to the receiving process.
ISSUE = r'''
import base64, json, subprocess, sys
profile, duration = sys.argv[1:]
k = ['/var/lib/rancher/rke2/bin/kubectl', '--kubeconfig', '/etc/rancher/rke2/rke2.yaml']
def run(args, payload=None):
    return json.loads(subprocess.check_output(k + args, input=payload, stderr=subprocess.DEVNULL))
try:
    ca = run(['-n', 'hermes-system', 'get', 'configmap', 'kube-root-ca.crt', '-o', 'json'])['data']['ca.crt']
    request = {'apiVersion': 'authentication.k8s.io/v1', 'kind': 'TokenRequest',
               'spec': {'expirationSeconds': int(duration)}}
    status = run(['create', '--raw', '/api/v1/namespaces/hermes-system/serviceaccounts/' + profile + '/token', '-f', '-'], json.dumps(request).encode())['status']
    identity = 'system:serviceaccount:hermes-system:' + profile
    config = {'apiVersion': 'v1', 'kind': 'Config', 'current-context': profile,
              'clusters': [{'name': 'home', 'cluster': {'server': 'https://k8s.home.finnes.dev:6443',
                  'certificate-authority-data': base64.b64encode(ca.encode()).decode()}}],
              'users': [{'name': profile, 'user': {'token': status['token']}}],
              'contexts': [{'name': profile, 'context': {'cluster': 'home', 'user': profile}}]}
    print(json.dumps({'profile': profile, 'identity': identity, 'expiry': status['expirationTimestamp'], 'config': config}))
except Exception:
    sys.exit('Credential issuance failed; no credential details printed.')
'''

# Run as the profile-file owner inside Hermes. Keep the old file until verification succeeds.
INSTALL = r'''
import json, os, subprocess, sys, tempfile
path = None
try:
    envelope = json.load(sys.stdin)
    profile = envelope['profile']
    if profile not in ('opslead', 'incident', 'observability', 'implementer', 'security', 'resilience'):
        raise ValueError('profile')
    directory = '/opt/data/profiles/' + profile
    fd, path = tempfile.mkstemp(prefix='.kubeconfig-', dir=directory)
    with os.fdopen(fd, 'w') as output:
        json.dump(envelope['config'], output)
    k = ['/usr/bin/mise', 'exec', 'aqua:kubernetes/kubectl@' + sys.argv[1], '--', 'kubectl', '--kubeconfig', path, '--request-timeout=15s']
    result = subprocess.check_output(k + ['auth', 'whoami', '-o', 'json'], cwd='/opt/data', stderr=subprocess.DEVNULL)
    identity = json.loads(result)['status']['userInfo']['username']
    if identity != envelope['identity']:
        raise ValueError('identity')
    subprocess.run(k + ['auth', 'can-i', 'get', 'pods', '--all-namespaces'], cwd='/opt/data',
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    # Seed gateway keys so native profile-scoped passthrough can resolve them.
    # Unscoped callers get an empty kubeconfig, never another profile's credentials.
    import contextlib, io
    for home, kubeconfig in ((directory, directory + '/kubeconfig.yaml'), ('/opt/data', '/dev/null')):
        os.environ['HERMES_HOME'] = home
        from hermes_cli.config import read_raw_config, save_env_value, set_config_value
        passthrough = read_raw_config().get('terminal', {}).get('env_passthrough', [])
        if not isinstance(passthrough, list):
            raise ValueError('invalid terminal.env_passthrough')
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            for name in ('KUBECONFIG', 'HERMES_KUBECONFIG'):
                os.environ.pop(name, None)
                save_env_value(name, kubeconfig)
                if os.environ.get(name) != kubeconfig:
                    raise ValueError('profile environment update failed')
                if name not in passthrough:
                    passthrough.append(name)
            set_config_value('terminal.env_passthrough', json.dumps(passthrough))
        if read_raw_config().get('terminal', {}).get('env_passthrough') != passthrough:
            raise ValueError('profile passthrough update failed')
    os.replace(path, directory + '/kubeconfig.yaml')
    path = None
    print(profile + ': ' + identity + ', expires ' + envelope['expiry'])
except Exception:
    sys.exit('Credential verification or installation failed; previous kubeconfig retained.')
finally:
    if path is not None:
        os.unlink(path)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=PROFILES, help='rotate one profile instead of all six')
    parser.add_argument('--duration-seconds', type=int, default=30 * 24 * 60 * 60)
    parser.add_argument('--host', '--satellite', dest='host', default='satellite',
                        help='verified SSH host with the existing Hermes container')
    args = parser.parse_args()
    if args.duration_seconds <= 0:
        parser.error('--duration-seconds must be positive')
    nas = ['ssh', '-F', '/dev/null', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
           '-i', os.path.expanduser('~/.ssh/ansible'), '-o', 'StrictHostKeyChecking=yes',
           '-o', 'HostKeyAlias=nas.home.finnes.dev', 'ansible@172.21.71.10']
    satellite = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                 '-o', 'StrictHostKeyChecking=yes', args.host]
    version = tomllib.loads((Path(__file__).resolve().parents[1] / '.mise.toml').read_text())['tools']['aqua:kubernetes/kubectl']
    for profile in (args.profile,) if args.profile else PROFILES:
        issued = subprocess.run(nas + [shlex.join(['sudo', '-n', 'python3', '-c', ISSUE,
                                  profile, str(args.duration_seconds)])], capture_output=True)
        if issued.returncode:
            sys.exit(profile + ': credential issuance failed; kubeconfig unchanged.')
        installed = subprocess.run(satellite + [shlex.join(['sudo', '-n', 'podman', 'exec', '-i',
                                    '--user', 'hermes', '-w', '/opt/data', 'hermes', 'python3', '-c', INSTALL, version])],
                                   input=issued.stdout, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        if installed.returncode:
            sys.exit(profile + ': verification/install failed; kubeconfig unchanged. Earlier profiles may have rotated.')
        print(installed.stdout.decode().strip())


if __name__ == '__main__':
    main()
