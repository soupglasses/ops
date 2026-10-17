<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
SPDX-License-Identifier: MIT
-->

# Application identity and settings reconciliation

Crossplane manages directory entries and typed application identities. Flux owns
those resources, application settings and deployment order. The LDAP provider uses
Upjet and the Terraform provider SDK; there is no Terraform CLI or state directory.
Existing Terraform-owned directory roots and applications retain their ownership.

## Jellyfin identity

The application namespace opts in to the cluster-owned `directory` backend. Its
central LDAP administrator credential stays in `identity/openldap`; application
credentials stay in `media/jellyfin-identity`. A central-provider Entry must be a
direct child of an allowed base, with an RDN prefixed by its namespace and a dot.
The generic Entry API reconciles only explicitly owned attributes. Group membership
is initialized with the directory administrator and subsequently managed in Canaille.

Jellyfin members sign in with their LDAP username or email address and password.
Both lookup attributes resolve to the same `uid` account identity. Keep email
addresses unique among members and distinct from other members' usernames; the
upstream plugin chooses the first result when aliases collide. The
unmodified upstream LDAP plugin links the account on login and synchronizes its
`jpegPhoto` through its existing scheduled task. Jellyfin does not need a separate
OIDC client or an SSO plugin for this path. The local `ops-recovery` administrator
remains available for emergency access and settings reconciliation.

LDAP password changes remain disabled. The plugin's `PasswordResetUrl` points to
Canaille's `/reset` page; Jellyfin displays it through its existing forgotten-password
dialog, which still continues to the PIN screen. That form requires the canonical
Jellyfin username (`uid`), not the email login alias. The web client's native
`menuLinks` also provides an **Account and password** link to Canaille. An init
container merges those settings into the image's stock web configuration, preserving
its themes and player plugins. No CSS, injected JavaScript or client fork is used.

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

### LDAP-only live acceptance

On 2026-10-17, the simplified runtime built from source `bc12af9` was published as
`ghcr.io/soupglasses/ops/jellyfin-runtime@sha256:ae736303e6c1ed9ca00824356042a1335e9ce98403b21934faf4e296b02a0a30`
and deployed through Flux. The retired SSO binary was absent. A disposable
directory member signed in with an LDAP password before any Jellyfin account
existed. The upstream LDAP task imported the exact fixture JPEG, and repeat login
returned the same Jellyfin user ID. The upstream photo task also had an automatic
interval trigger. The settings acceptance command and private
TLS health endpoint passed, and all Flux Kustomizations were Ready.

### Configuration restore evidence

On 2026-10-17, a fresh offline checkpoint containing the LDAP-linked test account
and its photo was backed up off-cluster at 18:34:36 UTC as snapshot `d3b05901`.
An independent ReplicationDestination restored that exact snapshot at 18:34:50 UTC
and populated a separate 20 GiB PVC. Generation `752f02fa492a4bf1a6ec70ec78ae6815`
contained 39 files. The restore helper verified every file hash and SQLite
integrity; the restored pre-start database SHA-256 was
`d4a3e9c041d0d2995d93bb758cd657402199d4920a8f48c704bb081376b3abaa`.

At 18:37:31 UTC, the matching pinned image passed an authenticated API comparison
against the source: the same server identity, both account IDs, three libraries,
six active plugins including LDAP 23 and no SSO, and the declared settings using
existing Secret references without printing credentials. The restored test user
retained the exact 632-byte JPEG, SHA-256
`ed8de651b6efa7e3f6a4c034e4f25d7539918037956fa29803aee994905073c5`.
The verifier used a restricted, network-isolated Job. The production PVC was not
mounted or replaced, and all temporary restore resources were removed.

The disposable account, LDAP link and group membership were removed after recovery
verification; the settings check passed again. The production destination refreshes
daily at 08:15 UTC. Operator-triggered refreshes must remove their manual trigger
afterward because an outstanding manual tag takes precedence over the cron schedule.

After fixture cleanup, clean backups succeeded at 18:40:13 UTC for Jellyfin
(snapshot `db88990b`) and 18:40:17 UTC for LDAP (snapshot `48bf607d`). Both production
destinations restored those exact clean snapshots at 18:40:37 UTC. The sources kept
their 06:15 schedules. Jellyfin kept its 08:15 destination schedule without a manual
trigger; LDAP's existing one-time trigger was preserved. The latter's lack of an
automatic refresh schedule is tracked separately in `TODO.md`.

### LDAP policy recovery

The offline policy initializer must mount `/data/db` at `/var/lib/openldap` as well
as mounting the directory PVC at `/data`. The persisted LDAP configuration refers to
that database path; even a configuration-only `slapcat` opens it. Omitting the alias
on a read-only container filesystem causes native initialization to fail before any
ACL change. The alias grants access to the same database already present under `/data`.

The stock-image policy was tested against an off-cluster
pre-change backup on 2026-10-17. Snapshot `cb535e08`, captured at 18:11:35 UTC,
restored through an independent ReplicationDestination and populated PVC. At
18:30:28 UTC, the current native policy had applied twice with identical effective
configuration, an unchanged MDB file and a byte-identical 92-entry native export
(SHA-256 `a419d1aaa188480e278c455e017c3af6370f19c4d91f5d3d5b0b6f5f6120ffff`).
The verifier used the stock production image, UID/GID 389, a read-only root
filesystem and denied network access. No production PVC or credentials were
mounted, and all temporary resources were removed. Native `slapmodify` refreshes
configuration timestamps even on a repeated apply; the idempotence check compares
effective configuration while the directory-data checks remain byte-for-byte.
