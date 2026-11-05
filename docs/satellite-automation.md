<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
SPDX-License-Identifier: 0BSD
-->

# Satellite automation and recovery

Satellite hosts the public status page independently of NAS and Kubernetes.
`ansible/playbooks/satellite-automation.yml` reproduces the bot credential broker,
Git defaults and ACME webroot permission fix. It runs locally on satellite by
default (`task ansible:satellite`); the hostname assertion prevents accidentally bootstrapping another host.

## Bot credentials and signing

The signing key is `~/.ssh/soupbot_signing`, owned by the automation user with mode
0600. Its public key and allowed-signers file live beside it. The key was recovered
from the legacy Hermes service's systemd credential after verifying that its public
key matched the repository automation signer. Normal signing now reads the local
key directly and does not require that service or a privileged signing wrapper.

The role sets the `soupbot` author and committer defaults, GitHub noreply address,
SSH signing format, trusted public keys and default commit/tag signing. Test the
defaults in a disposable repository with an empty commit and `git verify-commit`;
do not create verification-only commits in the infrastructure history.

GitHub authentication uses the soupbot-owned `soupbot-ai` App installed on
`soupglasses`. The app PEM is a separate credential from the SSH signing key. The
root-owned broker receives it through systemd encrypted credentials at
`/etc/credstore.encrypted/soupbot-github-app-private-key`. Private keys and tokens
must stay out of Git and logs. Keep independent encrypted backups of the signing
key and app PEM; a machine-bound encrypted credential alone is insufficient to
rebuild a lost host.

The broker source and tests are in `ansible/roles/soupbot_runtime/files`. Its service
is `soupbot-github-agent.service`; it implements the existing encrypted oidc-agent
socket protocol. The shared alias `/run/soupbot/oidc-agent.sock` points to
`/run/soupbot-github/oidc-agent.sock` and is recreated by systemd-tmpfiles. The
`oidc-shell` group controls socket access. The separate Home Assistant broker and
legacy OAuth state remain independent.

Use `~/.local/bin/gh` through the normal PATH, or explicitly provide a broker-issued
`GH_TOKEN` to the stock CLI. The wrapper and Git helper select the App broker
explicitly and fail closed. Do not print `oidc-token` output. For a direct shell
session using the stock client, select the shared socket with `OIDC_SOCK`.

Verify the actor with `gh api graphql -f 'query={ viewer { login } }' --jq
.data.viewer.login`; it must be `soupbot-ai[bot]`. Installation tokens do not support
`GET /user`. Check repository access through `installation/repositories`; request a
new token after repository installation changes. Missing bot permissions require
fixing the App installation or asking the owner; never switch to a human account.
Creating personal-account repositories may require the owner to create an empty
repository in GitHub's UI before the bot pushes code.

## Certificate renewal

Nginx serves HTTP-01 files from `/var/www/acme/.well-known/acme-challenge` on both
address families. The renewal template is `/etc/systemd/system/lego-renew@.service`,
with the daily instance timer enabled for the status domain. Account email comes
from `/etc/lego-email`, account/certificate data from `/var/lib/lego`, and the nginx
site from `/etc/nginx/sites-enabled/status.finnes.dev`.

The failed IPv6 validation reached the correct host but received 403: umask 0027
created challenge files nginx could not read. The role creates the public challenge
directory as root:www-data with mode 2750 and installs the `ExecStartPre` drop-in.
New files inherit www-data while the restrictive private-key umask remains intact.
Certificate keys remain root:lego with mode 0640. Do not make private keys public
or weaken the renewal unit's umask to fix public challenge-file access.

After changes, test a temporary challenge file over IPv4 and IPv6, remove it, and
check the real renewal service result and served certificate. Use explicit public
DNS resolution when local `/etc/hosts` aliases hide an address family. Reload nginx
after successful renewal; the existing renewal hook does this automatically.

The role supplies the permission repair. A new host also needs nginx, lego, initial
account/certificate provisioning and the domain's A/AAAA records. Those inputs
must be restored or provisioned before enabling the public site.

## Monitoring ownership

The existing Kuma server uses `/etc/containers/systemd/uptime-kuma.container`, a
local MariaDB database named `uptime_kuma`, the database socket mounted from
`/run/mysqld`, and persistent uploads under `/var/lib/uptime-kuma/data`. Preserve the
database and uploads when rebuilding satellite. Their host-side provisioning and
backup credentials remain prerequisites, rather than Kubernetes resources.

The native provider is maintained in `soupglasses/provider-uptimekuma`; ops only
contains its pinned installation and application declarations. Its generated
Crossplane runtime is set to zero replicas because Crossplane automatically binds
broad Secret permissions to package-owned Deployments. An independent Deployment
uses the same package image and a namespaced Role. Kustomize copies the image
reference from the Provider so both remain on the same verified artifact.

HTTP and native GameDig monitors are adopted by unique application name; public
pages use their unique slug. The page composition resolves current monitor IDs,
rejects another namespace/backend and preserves the last page while references
are pending. Management policies omit Delete to preserve data on Kubernetes
resource removal. Test installation, restart, adoption and complete control-plane
outage behavior in the provider's disposable CI fixtures.

The former Hermes monitor was deleted manually at the owner's request. It is
absent from the desired page configuration; the Hermes application was not stopped.
Auth and Minecraft remain the managed public checks. The Kuma metrics key cannot
manage configuration: the normal login is encrypted in the new local SOPS Secret.
Never decrypt existing SOPS files to recover it; use the independently held login
or have the owner provision a new assigned credential.

The Cilium host-policy ownership handoff remains tracked in ops issue #30. Its live
policy is enforced while Flux adoption is suspended; this automation role does not
perform that handoff.
