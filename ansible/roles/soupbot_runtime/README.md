<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
SPDX-License-Identifier: 0BSD
-->

# Soupbot automation on satellite

This role installs the GitHub App token broker, stock oidc-token integration, Git
credential helper, fail-closed GitHub CLI wrapper, SSH signing defaults and public
ACME challenge permissions. It preserves existing encrypted app credentials and
signing keys. It never substitutes a human GitHub credential.

Run `satellite-automation.yml` on satellite. The inventory uses a local connection
and the role checks the hostname before changing local credentials. To provision
through SSH, override the connection explicitly and use a verified host key.

On a fresh host, supply `soupbot_runtime_signing_key_source` and
`soupbot_runtime_github_app_key_source` as paths to independently held private keys on the
Ansible controller. Keep these inputs outside Git. The role installs the signing
key under the user's private `.ssh` directory and seals the app key with
systemd-creds. It does not overwrite existing keys or perform automatic rotation.
The public signing key must match the repository's `.allowed_signers` trust file.

The bot user signs commits by default, using the key file directly. GitHub HTTPS
authentication uses installation tokens through the broker. These are separate
keys and separate purposes; SSH signing does not change NAS login credentials.
The user-local `gh` wrapper obtains a fresh installation token for every command
and fails if the broker is unavailable. `/usr/bin/gh` remains the stock CLI.

The role configures the public challenge directory and a renewal-service drop-in.
Existing nginx/lego installation, certificate account state, Kuma Quadlet and
MariaDB hosting are separate prerequisites; see `docs/satellite-automation.md`.
