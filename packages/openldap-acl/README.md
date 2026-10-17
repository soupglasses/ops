<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
SPDX-License-Identifier: MIT
-->

# OpenLDAP application profile ACL

This image extends the pinned production OpenLDAP 2.6 image with Python's standard
library and a small offline configuration tool. It does not contain application
credentials. New code is MIT; the base image retains its upstream licenses.

A Flux-managed LDIF describes one application's group-filtered user-profile read
grant. The init container runs as the existing directory UID/GID 389, after the
restored-directory check and before slapd or TLS reload starts. `slapmodify`
updates the native configuration and regenerates its checksums; generated LDIF
files are never edited by hand. The tool reads native configuration only in
memory and suppresses tool output that could contain credential hashes.

The managed rule is identified by its service DN and subtree/group-filtered read
shape. Other rules retain their relative order, including legacy readers, self
access, and TLS reload permissions. Duplicate ownership or a missing first
password-protection rule prevents startup. The profile grant sits immediately
after that password rule and before broad legacy profile denials. Its explicit
attribute allowlist excludes passwords and OAuth secrets; the group filter
limits photos to application members. Changes to the application's selected
profile attributes are authoritative at this position.

Policy is reconciled on startup and when a generated policy ConfigMap changes
and rolls the Deployment. Runtime edits are not periodically overwritten. This
tradeoff avoids relying on LDAP assertion controls, which the pinned back-config
backend rejects, and avoids granting a new runtime principal ACL administration.
Use Git for policy changes and restart the Deployment to repair runtime drift.

## Lifecycle and backups

A single slapd holds `.slapd.lock` exclusively and `.configuration.lock` shared
on its configuration PVC. The offline init takes both locks exclusively in that
order, with a bounded wait. Thus a replacement Pod cannot edit configuration
while a terminating Pod's slapd still uses it, even when both mount the same RWO
volume on one node. Foreground slapd inherits both descriptors through shell
`exec`, keeping normal PID 1 signal handling.

Native backups share `.configuration.lock` across both exports, allowing online
backups while preventing an offline init from changing their configuration.
Backup and TLS reload additionally serialize online configuration export/reload
through the Pod's existing `/run/openldap/configuration.lock`.

The first rollout from an older, unlocked Deployment must use its existing
`Recreate` strategy and a fresh verified backup. Subsequent startup and direct
Pod replacement use the persistent locks. Restoring an older native backup that
lacks the application ACL is supported: the init recreates it before serving.
The UID 389 bootstrap is trusted directory-control-plane code because it already
has write access to the configuration PVC.

## Verification

```sh
docker build -t ops-openldap-acl:development packages/openldap-acl
OPENLDAP_ACL_TEST_IMAGE=ops-openldap-acl:development \
  python3 -m unittest discover -s packages/openldap-acl -v
```

The real-backend test uses exactly the image's native tools and startup wrapper.
It restores an older native configuration, verifies idempotence and unchanged
legacy/TLS ACLs, proves offline edits and duplicate daemons are excluded while
slapd runs, exports a backup during service, and checks actual application binds:
members can fetch JPEGs, nonmembers cannot, and user passwords remain hidden.
