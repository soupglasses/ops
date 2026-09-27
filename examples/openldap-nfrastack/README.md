<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# OpenLDAP image migration example

This example exercises migration from the pinned `soupglasses/openldap:2.6.10`
image to `nfrastack/openldap:2.6-8.1.1` using synthetic accounts. It does not change
the production Deployment, credentials, or PVC. It retains the Finnes directory
suffix, Canaille OAuth schema, OTP support, memberOf/refint, and password policies.

The [upstream image](https://github.com/nfrastack/container-openldap) supplies the
OpenLDAP binaries and modules. Its `/init` entrypoint needs root and capabilities
that the cluster's restricted Pod Security policy rejects. The Docker drill tests
that initializer for compatibility, then also tests `prepare.sh`: a one-time,
rootless conversion of `prepare.conf` using packaged `slaptest` and `slapadd`.
Kubernetes preparation uses this rootless path. The steady-state runtime runs the packaged
`slapd` directly as UID/GID 389, with no privilege escalation, no capabilities, and
a read-only root filesystem. No replacement image or recurring data-layout shim
is required.

This reduces image maintenance, but does not outsource directory policy:
`cn=config`, schema, ACLs, and migrations remain operator-managed. The upstream
environment-variable initialization is deliberately absent from normal restarts.
Changes to `bootstrap/*.ldif` apply to **new configuration only**, not an existing
PVC. Future policy changes require reviewed `ldapmodify` operations or offline
configuration migration, followed by export and a restore drill. Image upgrades
must rerun this example against the candidate digest; do not assume `/init` will
upgrade persisted configuration. Administrator password rotation also needs an
explicit update of persisted `olcRootPW` using a maintenance job; changing the
Kubernetes Secret and restarting no longer rotates it. Keep that operation coupled
to the credential change and a fresh native configuration backup. The restricted
certificate reloader deliberately cannot change administrator credentials.

## Run the migration drill

Requirements: Python 3, OpenSSL, Docker, and registry access. Run from the repo root:

```sh
python3 examples/openldap-nfrastack/test.py
```

The test creates random synthetic credentials and certificates, publishes no host
ports, uses an internal Docker network, and removes its containers and temporary
data on completion. It checks:

- Password authentication and group membership through certificate-verified LDAPS;
  a separate client cannot connect to port 389.
- Read-only account searches and group reads; writes and reads of password hashes,
  populated OTP secrets, and OAuth secrets are denied to that account.
- Argon2 password storage, OAuth/OTP schema, and the existing password policies.
- Certificate replacement with local socket reload, without restarting the server.
- Persistence across container replacement and restoration into empty config/db
  directories using native exports.
- Export from the legacy pinned image and import into the new configuration,
  preserving entryUUID, stored hashes, authentication, groups, OTP, and OAuth data.
- The restricted non-root runtime and rejection of unrelated configuration writes
  by the certificate reloader.

To retain a successful synthetic fixture for interactive use:

```sh
python3 examples/openldap-nfrastack/test.py --keep
# Set LDAP_TEST_DIR to the path printed by the test.
export LDAP_TEST_DIR=/tmp/ops-ldap-nfrastack-REPLACE
docker compose -f examples/openldap-nfrastack/compose.yaml up -d
docker compose -f examples/openldap-nfrastack/compose.yaml down
```

Remove the retained directory when finished; its database files belong to UID 389.
The retained fixture includes synthetic credential files and private keys: keep it
local, do not commit it, and never substitute production data into this test.

## Kubernetes preparation

[render-preparation.py](render-preparation.py) renders the one-time staging PVC,
restricted preparation Pod, configuration, and deny-all NetworkPolicy:

```sh
python3 examples/openldap-nfrastack/render-preparation.py > /tmp/ldap-preparation.json
kubectl apply --dry-run=server -f /tmp/ldap-preparation.json
```

This renderer is specific to the repository's `identity` namespace, storage class,
and existing admin/TLS Secrets. The staging PVC is now a retained rollback copy:
**do not reuse it for a new migration**. A new rehearsal needs distinct resource
names and empty storage. Supply the legacy native export as
`/data/source/directory.ldif`, then run `/bin/sh /source/prepare.sh` inside the
preparation container. Preparation refuses existing configuration or data.

[verify-live.sh](verify-live.sh) validates an isolated running candidate using its
mounted admin credential. It briefly creates a disposable reader account, checks
binds, searches, ACL denials, and TLS, then removes the probe. It is a migration
test, not a production health check. See the [migration record](../../docs/openldap-nfrastack-migration.md)
for backup, restore, and cutover ordering.

## Kubernetes runtime and TLS

```sh
python3 examples/openldap-nfrastack/render-kubernetes.py > /tmp/ldap-example.json
kubectl apply --dry-run=server -f /tmp/ldap-example.json
```

The renderer produces an isolated Pod, Service, ConfigMap, and deny-all
NetworkPolicy in `identity`. It expects an already prepared PVC named
`openldap-nfrastack-example-data`; it does not create, initialize, or restore one.
Consequently, server dry-run checks admission compatibility, not a live migration.
The Pod refuses to start without both restored configuration and a database.
The NetworkPolicy intentionally permits no application access; add specific client
rules when integrating the runtime into the production Deployment.

The existing [Certificate](../../kubernetes/apps/identity/openldap/app/certificate.yaml)
already requests `ldap.finnes.dev` from cert-manager. The example mounts its
`openldap-tls` Secret as a directory, mode 0440 with fsGroup 389. A sidecar checks
for certificate changes every 30 seconds and reloads both certificate paths in a
single local SASL EXTERNAL operation, retrying failures. It mounts only the socket,
script, and certificate, with no PVC or Kubernetes API credentials. Its exact
UID/GID peer identity may write only the two TLS path attributes in `cn=config`.

Only LDAPS is exposed: Service port 636 forwards to unprivileged container port
1636. Port 389 and STARTTLS are disabled; there is no cleartext network listener
in the normal runtime. The Unix socket remains local. TLS requires version 1.2 or
newer and security strength 128. Clients must verify the CA chain and the hostname
`ldap.finnes.dev`; password bind over verified TLS remains supported. Client
certificates are not required. See the
[OpenLDAP TLS guide](https://www.openldap.org/doc/admin26/tls.html).

During isolated preparation, upstream briefly opens an LDAP listener on loopback;
`settings.env` confines it there. The production runtime never runs that bootstrap.

## Application read access

Membership of `cn=ldap-readers,ou=groups,dc=finnes,dc=dev` grants selected identity
attributes under `ou=users` and membership attributes under `ou=groups`. Configure
Nextcloud with those separate search bases, `ldaps://ldap.finnes.dev:636`, and a
bind DN such as `uid=nextcloud,ou=system,dc=finnes,dc=dev`. Suffix-based searches are
also supported, but expose only the suffix entry and
these allowed user/group attributes. Service accounts use the existing
`cn=system,ou=policies,dc=finnes,dc=dev` password policy; they cannot change their own
password. RootDN still has administrative access for existing Canaille/Terraform.

Production delegates these reads to the existing `uid=auth,ou=system,dc=finnes,dc=dev`
account through `ldap-readers`. The test creates separate synthetic fixtures. Migrating real
data does not create them automatically: provision the group, service account,
policy reference, and membership through the chosen account-management workflow
before switching an application's bind DN. That workflow and the Terraform admin
membership ownership bug are separate from replacing the server image.

## Stable account identity

Preserve `entryUUID` as the stable account and group identifier, with `uid` values
such as `sofie` and `lcp` available for human logins. The reader ACL exposes
`entryUUID`, and the migration checks it across all entries. Existing LDAP DNs
remain unchanged; `groupOfNames.member` and `memberOf` use DNs by schema.

For a new Nextcloud LDAP connection, set `ldapExpertUUIDUserAttr` and
`ldapExpertUUIDGroupAttr` to `entryUUID`, leave `ldapExpertUsernameAttr` empty to
use UUID-derived internal IDs, and use a login filter such as
`(&(objectClass=inetOrgPerson)(uid=%uid))`. See the
[Nextcloud LDAP settings](https://docs.nextcloud.com/server/stable/admin_manual/configuration_user/user_auth_ldap.html#expert-settings).
These settings affect new mappings. Do not clear existing mapping tables or
recreate users: existing application identity/data migrations need separate review.
There is no deployed Nextcloud resource in this repository to configure yet.

## Production cutover contract

A production migration must preserve the existing data and recoverability:

1. Quiesce writers and take a native export from the old image using its existing
   `slapcat -f /config/slapd.conf`. Keep the old PVC and a validated off-cluster
   recovery point. Export/import is preferable to copying an LMDB file across
   builds; the drill tests this boundary.
2. Prepare new configuration with the Canaille schema, modules, TLS, and reviewed
   ACLs. Feed the existing admin credential through a mounted Secret, without
   displaying, copying between secret files, or committing its value. Import the
   old directory using the new image's `slapadd`. Do not import the old server
   configuration over the new configuration. Own both directories as UID/GID 389.
3. Persist **both** `/data/config` and `/data/db` on the same new PVC. Mount `db`
   additionally at `/var/lib/openldap`, the path in the generated configuration.
   Give `/run/openldap` an emptyDir with a writable `run/` subdirectory. Keep the
   startup guard: an empty recovery must fail rather than create an empty directory.
4. Adapt the standard VolSync component to protect the whole new `/data` layout,
   including both native exports (`slapcat -F /data/config -n 0` and `-n 1`).
   Publish the pair only after both exports succeed; withhold
   backup triggers on failure. LMDB supplies transaction-consistent native exports;
   do not race configuration migrations with backup. Update mover ownership to 389 and use the existing
   `ReplicationDestination`/PVC `dataSourceRef` restore contract. Preserve retention,
   off-cluster storage, and failed/stale backup alerts.
5. Restore the **new off-cluster backup** into another PVC. Verify real account
   counts, stable IDs, password binds, groups, OAuth/OTP attributes, and reader ACLs.
   Obtain resilience and security review before switching the existing Service.
   Keep the old PVC through acceptance; after new writes, rollback needs a fresh
   export rather than silently reverting users to an older directory.
6. Apply the Flux-managed Deployment and verify Canaille and application logins,
   TLS hostname/chain and renewal, closed port 389, and fresh restorable backups.
   Delete temporary resources. Do not leave the example Pod as production state.

Recovery needs the repository, its documented SOPS/backup recovery keys, the
existing off-cluster backup endpoint/credentials, and a fetchable pinned image.
Use [the VolSync contract](../../kubernetes/components/volsync/README.md) and
[the existing LDAP migration procedure](../../docs/openldap-volsync-migration.md)
for recovery-point selection and independent survival. Before production cutover,
archive the pinned image in independently recoverable storage or document and test
rebuilding its exact upstream source tag and base dependencies; a registry digest
alone is not an image recovery plan.

## Validation status

Production migrated on **2026-09-27** after a real off-cluster backup was restored
into `openldap-directory` and its directory/configuration contents validated.
The Docker drill separately passed synthetic migration, authentication, restricted
runtime, restore, and automatic certificate-reload tests. Flux and Canaille are
healthy, and the first backup from the production volume succeeded. Original and
staging PVCs remain available as rollback copies.

The [production Deployment](../../kubernetes/apps/identity/openldap/app/deployment.yaml)
and [migration/recovery record](../../docs/openldap-nfrastack-migration.md) describe
the deployed configuration and exact verification evidence.
