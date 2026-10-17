<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
SPDX-License-Identifier: MIT
-->

# Application identity and settings reconciliation

Crossplane manages directory entries and typed application identities. Flux owns
those resources, application settings and deployment order. The LDAP provider uses Upjet and
the Terraform provider SDK; there is no Terraform CLI or state directory.
Existing Terraform-owned directory roots and applications retain their ownership.

## Jellyfin identity

The application namespace opts in to the cluster-owned `directory` backend. Its
central LDAP administrator credential stays in `identity/openldap`; application
credentials stay in `media/jellyfin-identity`. A central-provider Entry must be a
direct child of an allowed base, with an RDN prefixed by its namespace and a dot.
The generic Entry API reconciles only explicitly owned attributes. Group membership
is initialized with the directory administrator and subsequently managed in Canaille.

Jellyfin members sign in with their normal LDAP username and password. The
unmodified upstream LDAP plugin links the account on login and synchronizes its
`jpegPhoto` through its existing scheduled task. Jellyfin does not need a separate
OIDC client or an SSO plugin for this path. The local `ops-recovery` administrator
remains available for emergency access and settings reconciliation.

OpenLDAP server policy belongs to its existing deployment:
[`access-policy.ldif`](../kubernetes/apps/identity/openldap/app/access-policy.ldif)
is the authoritative MDB access policy, including the legacy reader/self rules
and the narrow Jellyfin group-member profile/photo grant. A stock-image init runs
native `slapmodify` before the server starts. The generated ConfigMap rolls the
Pod when policy changes. No separate ACL package, custom image or controller is
required. Change server ACLs in Git; runtime ACL changes are replaced at startup.

The Upjet provider manages ordinary directory entries using the existing Terraform
LDAP SDK. Its `OAuth2Client` composition remains available for OIDC applications
and maps that API to the configured backend's LDAP schema. Jellyfin currently
uses only its bind-account and group entries. Deleting an Entry deliberately
retains the directory object; permanent deletion is an explicit administration action.

## Application settings

Jellyfin's configuration ConfigMap declares plugin settings, login branding, network
settings and library paths. The sidecar reads the current API values every minute,
merges the owned subset and repairs drift. It preserves user mappings, photo hashes
and other unmanaged state. Credentials are mounted files; API tokens remain in
memory and are not written to Kubernetes status or logs.

The sidecar is a bounded exception to Crossplane resource management. See
[the runtime rationale](../packages/jellyfin-runtime/CONFIGURATION.md) for the
upstream HTTP provider's credential-handling problem and pinned evidence. The
existing Jellyfin Terraform provider is MPL-licensed; its code is not vendored here.

New authored integration code is MIT. Generated Upjet CRDs keep their upstream
license through generated sidecars. Upstream Jellyfin/plugin binaries retain their
own licenses in the image. Regenerate CRDs using the chart README; do not edit
generated files to add licensing text.

## Acceptance tests

```sh
# Read-only settings comparison against the running application's API.
task app:test NAMESPACE=media APP=jellyfin
# Read-only provider/API smoke checks through the chart's Helm hook.
task app:test NAMESPACE=identity RELEASE=ldap-provider
# Verify final Flux rendering, including checkpoint selection and media retention.
task app:verify-render
```

A Deployment declares its acceptance container and argument array with
`testing.ops.finnes.dev/container` and `testing.ops.finnes.dev/command` annotations.
The runner waits for rollout, then executes that command without a shell. Tests
must preserve application configuration and data; authentication may create a
short-lived API session. Execution uses the operator's Kubernetes permissions.
There is no additional in-cluster test controller or elevated testing account.

Helm charts keep native test hooks and Flux `HelmRelease.spec.test.enable`. The LDAP
chart defaults to smoke mode when enabled. Integration mode creates synthetic
identities and restarts its controller, so it is reserved for disposable kind:

```sh
docker build --target runtime -t ops-provider-ldap:development packages/provider-ldap
docker build --target tests -t ops-provider-ldap-tests:development packages/provider-ldap
packages/provider-ldap/kind-test.sh
```

The runner creates its own cluster and removes it on exit. Its successful test Pods
remain until their logs have been collected, then are deleted. CI runs these real
Helm/Crossplane tests plus Jellyfin/OpenLDAP image and recovery tests before image
publication. Production manifests pin anonymously pullable image digests; source
revisions and the build recipes are retained in this repository.

## Storage and recovery

Media uses the dedicated compressed 1 TiB ZFS dataset and NFS CSI stable `library`
directory. Jellyfin can write media sidecar files. Its database, accounts and artwork
under `/config/server` use a separate VolSync-backed PVC. Checkpoints stop Jellyfin,
validate and atomically publish an archive, then resume it before snapshots are
released. The restore destination refreshes its latest snapshot daily after the
scheduled backup, so PVC recreation does not remain pinned to the initial bootstrap
snapshot. A per-start guard validates or restores media; it never overwrites
surviving initialized data. See [media recovery](media-storage.md) and
[configuration recovery](../packages/jellyfin-runtime/README.md).

The private Gateway is `172.21.68.11/32` through the existing BGP sessions. It has no
WAN NAT mapping. Router source/interface rules and Service source ranges limit
HTTPS access to the trusted LAN, VPN and NAS. See
[private Gateway operations](../kubernetes/apps/network/cilium-private-gateway/README.md).

Inside the cluster, `auth.finnes.dev` resolves to the public Gateway's BGP VIPs
through the RKE2 CoreDNS configuration. This avoids WAN hairpin routing while
retaining the public issuer hostname and verified TLS for OIDC applications.
This cluster DNS setting is independent of Jellyfin's LDAP login.
Converge the DNS manifest independently of an RKE2 upgrade with
`ansible-playbook playbooks/configure-cluster-dns.yml` from `ansible/`.

### Configuration restore evidence

On 2026-10-17, a fresh offline Jellyfin checkpoint was backed up off-cluster at
17:28:06 UTC. Generation `bcd4a6bf08914209a5e55fd42f8d7139` contained 42 files and
`data/jellyfin.db`, whose SHA-256 was
`9753b44953d909f5cb994cf5081463cacb6990a5cc2bd33d29f644f0f73a6f22`.

A separate ReplicationDestination and populated 20 GiB PVC exercised the normal
snapshot/populator recovery path. The restore helper verified every file hash and
SQLite integrity before starting the matching pinned Jellyfin image in a restricted,
network-isolated Job. The recovered API matched the source server identity, account
identity, three configured libraries and seven active plugins, including LDAP 23 and
the then-installed SSO 4.0.0.4. This baseline predates the LDAP-only simplification. The read-only configuration
check also validated owned settings against the existing Secret references without
printing credentials or API tokens.

The restored database contained one user, seven base items, 72 permissions and
39 preferences. This initial checkpoint contained no user avatar; avatar recovery
requires a subsequent checkpoint after the live photo acceptance test. The original
application PVC remained untouched throughout the drill.

Temporary Jobs, restore PVCs, the test destination and its snapshots, and the test
network policy were deleted afterward. The production destination was refreshed at
17:37:57 UTC to `volsync-jellyfin-dst-dest-20261017173757`. Its manual trigger was
removed and the next scheduled refresh was confirmed for 2026-10-18 at 08:15 UTC.
An outstanding manual tag takes precedence over a cron schedule in VolSync; keep
only the schedule after completing an operator-triggered destination refresh.

### LDAP policy recovery

The offline policy initializer must mount `/data/db` at `/var/lib/openldap` as well
as mounting the directory PVC at `/data`. The persisted LDAP configuration refers to
that database path; even a configuration-only `slapcat` opens it. Omitting the alias
on a read-only container filesystem causes native initialization to fail before any
ACL change. The alias grants access to the same database already present under `/data`.

On 2026-10-17, an isolated VolSync destination/populator restored the real pre-migration
backup using cutoff `2026-10-17T17:13:43Z`. With UID/GID 389, a read-only root filesystem
and the then-current trial image, the original mounts reproduced the failure.
Adding only the database alias made native policy initialization succeed and a
second pass converge without changes. The directory database remained byte-identical
with SHA-256 `deaf3dfd01f46f5e66e3cdf37ec1a62f2d00a6c53f27cd4698c09cc7e8e1adc2`.
Its native export exactly matched the paired backup: 89 entries, SHA-256
`3a6f4e5ffae23f300c76094ce6de638629a705459392988837282aa17fd61f1a`.
The diagnostic mounted no production PVC, printed no configuration or credential
values, and all temporary resources were deleted afterward.
