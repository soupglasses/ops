#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: 0BSD
"""Converge bot Git defaults without printing existing credential configuration."""
import argparse
import os
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument('--home', type=Path, required=True)
parser.add_argument('--check', action='store_true')
args = parser.parse_args()
config = args.home / '.gitconfig'
settings = {
    'user.name': ['soupbot'],
    'user.email': ['306269487+soupbot@users.noreply.github.com'],
    'gpg.format': ['ssh'],
    'user.signingkey': [str(args.home / '.ssh/soupbot_signing')],
    'gpg.ssh.program': ['/usr/bin/ssh-keygen'],
    'gpg.ssh.allowedSignersFile': [str(args.home / '.ssh/allowed_signers')],
    'commit.gpgsign': ['true'],
    'tag.gpgsign': ['true'],
    'credential.https://github.com.helper': ['', '/usr/local/libexec/git-credential-soupbot'],
    'url.https://github.com/.insteadOf': ['git@github.com:', 'ssh://git@github.com/', 'ssh://git@github.com:22/'],
}
os.umask(0o077)
changed = False
for key, expected in settings.items():
    command = ['git', 'config', '--file', str(config)]
    result = subprocess.run(command + ['--null', '--get-all', key], capture_output=True, check=False)
    if result.returncode not in (0, 1):
        raise SystemExit('Could not inspect Git configuration')
    actual = result.stdout.decode().split('\0')[:-1]
    if actual == expected:
        continue
    changed = True
    if args.check:
        continue
    result = subprocess.run(command + ['--unset-all', key], capture_output=True, check=False)
    if result.returncode not in (0, 5):
        raise SystemExit('Could not reset the managed Git setting')
    for value in expected:
        subprocess.run(command + ['--add', key, value], check=True, capture_output=True)
print('changed' if changed else 'ok')
