<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# OpenLDAP migration and recovery

The Nfrastack replacement runs the maintained OpenLDAP binaries directly as UID/GID
389 under restricted Pod Security. One-time preparation converts the repository's
standard configuration into persistent `cn=config`; normal starts need no root
initializer, custom image, or repeated data conversion.

The complete runnable [example](../examples/openldap-nfrastack/README.md) includes
Docker migration tests, a Kubernetes preparation renderer, native import scripts,
and live candidate validation. Production resources are in
[the application directory](../kubernetes/apps/identity/openldap/app), with the
standard VolSync components selected by [its Flux configuration](../kubernetes/apps/identity/openldap/ks.yaml).

## Runtime contract

| Item | Configuration |
| --- | --- |
| Workload / Service | `identity/openldap` |
| Image | `docker.io/nfrastack/openldap:2.6-8.1.1` |
| Pinned index | `sha256:5e5c8fdd1d7e3a9623500839699b258d0569bf965528b85c682ec5d0902f5d6c` |
| Persistent volume | `openldap-directory` |
| Backup repository | `volsync-openldap-directory`, at the existing S3 backup endpoint |
| Persistent state | `/data/config` and `/data/db`; `db` also mounts at `/var/lib/openldap` |
| Native exports | `/data/backup/current/{config,directory}.ldif` |
| Recovery image | `/data/artifacts/nfrastack-openldap-amd64.tar`, checked by `SHA256SUMS` |
| Network endpoint | `ldaps://ldap.finnes.dev:636`, forwarded to container port 1636 |
| TLS | cert-manager Secret `openldap-tls`; TLS 1.2/1.3, hostname/CA verification |
| Cleartext LDAP | No port 389 listener; local administration uses the Unix socket |
| Renewal | UID/GID 389 sidecar reloads only the certificate/key attributes over LDAPI |
| Rollback claims | `openldap` and `openldap-nfrastack-stage`, retained with pruning disabled |

The sidecar mounts no directory/configuration PVC and has no Kubernetes API token.
The server's root filesystem is read-only; all containers drop capabilities and
forbid privilege escalation. Startup fails if restored configuration or the database
is absent. The certificate Secret is projected as a directory so renewal updates
reach the watcher.

Both configuration and data are backed up with mover UID/GID 389. `backup.sh`
serializes exports and publishes the complete pair atomically before the VolSync
copy trigger is released. LMDB gives the directory export a consistent transaction
view; this is not a claim that ordinary backups stop LDAP writes. Configuration
migrations must not overlap backups. Retention remains seven daily/four weekly
recovery points, with the existing failed/stale-backup monitoring.

## Identity and application access

All original DNs, entryUUIDs, password hashes, OAuth/OTP attributes, and memberships
are preserved. The added `ldap-readers` group delegates selected user-profile and
group-membership reads to the existing `uid=auth,ou=system,dc=finnes,dc=dev` account.
It does not expose password hashes, OTP secrets, OAuth data, or write access.
Suffix-based searches remain supported through a narrow suffix-entry ACL.

New applications should use `entryUUID` as their stable identity key and `uid` for
human login names. Existing account DNs are unchanged. Nextcloud mapping settings
are documented in the example; no Nextcloud deployment exists in this repository.
Canaille still uses its existing administrative bind. Its privilege scope and the
Terraform admin-membership ownership issue remain separate follow-up work.

## Migration evidence — 2026-09-27

Before switching, the old server was frozen and writers suspended. Its native
export exactly matched the imported original directory: 88 entries, including all
identity, credential, OTP, and OAuth attributes. The source export SHA-256 was
`f30978cdfb008857fac1d9b86d20f07f78a09f11e8d3511518a60cb52539d7ca`.

The candidate contained 89 entries after adding the reader group. Disposable test
accounts were removed before backup. Its unwrapped native directory export SHA-256
was `bcfd00c8e614c72f84809a6c0853912430f1331b8dc8c3af9cdd056bbaad8895`.

- Seed backup completed successfully at **09:21:41 UTC**.
- A separate final PVC restored from the off-cluster repository at **09:22:42 UTC**,
  using snapshot `volsync-openldap-directory-dst-dest-20260927092242`.
- The restored directory matched the recorded candidate digest exactly. Restored
  configuration and directory dumps each matched fresh native exports.
- The restored image archive passed its SHA-256 check and archive-readability check.
- Existing admin authentication succeeded with the mounted credential over verified
  LDAPS. A disposable reader authenticated and searched, but could not write or
  read password/OTP/OAuth secrets. The probe and its group reference were removed.
- TLS 1.2 and 1.3 succeeded; TLS 1.0/1.1 and port 389 were rejected.
- Synthetic tests also proved old-image export/import, UUID/hash preservation,
  fresh native restore, non-root preparation/runtime, and automatic certificate
  renewal through Kubernetes-style projected-Secret symlink rotation.
- Independent security and resilience reviewers approved cutover after these checks.

Production cutover and its first production-volume backup are verified separately
below when completed. No end-user plaintext password was requested or inspected;
real user-password/OTP contents were compared by aggregate directory digest, while
password-bind behavior was exercised with synthetic/disposable accounts.

## Recovery

A clean rebuild needs this repository, the documented SOPS decryption key, the
restic repository password and S3 credentials/endpoint recoverable from the existing
cluster recovery material. No new independent credential was introduced. Do not
copy decrypted credentials into this repository or expose them in diagnostics.

Flux creates `openldap-directory` from `openldap-directory-dst` using the standard
VolSync volume-populator contract. Restore both `config/` and `db/`, including their
UID/GID 389 ownership. For a fresh PVC on an existing cluster, refresh the frozen RD
image through the repository helper:

```sh
NS=identity WORKLOAD=deployment/openldap FLUX_KS=openldap \
  scripts/volsync-restore.sh openldap-directory
```

Use the helper's recovery-point selection options for an older known-good snapshot;
see [the VolSync contract](../kubernetes/components/volsync/README.md). Validate LDAP
contents and binds after restoration. A Bound PVC is not proof of usable data.

If native import is needed, restore `backup/current/config.ldif` with `slapadd -n 0`
and `backup/current/directory.ldif` with `slapadd -n 1` into empty target directories,
using the pinned image and the same `/var/lib/openldap` database mount. Never import
into the only known-good copy. The runnable Docker drill exercises this path.

The archived image is a standard Docker image archive for the cluster's amd64
architecture. Verify `SHA256SUMS`, load it with `docker load`, then publish it to a
reachable recovery registry or import it into the replacement node's runtime.
A mirrored single-platform manifest can have a different digest from the upstream
multi-platform index: pin the recovered manifest explicitly and validate it against
an isolated restored directory before use. The upstream source/build reference is
[nfrastack/container-openldap](https://github.com/nfrastack/container-openldap).
Refresh the archive and repeat recovery validation when changing the pinned image.

The old-format history remains in `volsync-openldap` and the original `openldap`
PVC. Restoring that history requires the prior 2.6.10 image/configuration from Git
history; it is not input for the new runtime's automatic restore. Both local
rollback claims remain explicitly managed by Flux. Retire them only after acceptance.
After production writes resume, reverting to their frozen data would discard later
changes: preserve a fresh export and the current PVC before any rollback.

Administrator password rotation now requires updating persisted `olcRootPW` as well
as the Kubernetes credential. Restarting the server does not rerun initialization
or read the old password environment variable. Use a reviewed maintenance operation
and take a fresh configuration/data backup afterward.
