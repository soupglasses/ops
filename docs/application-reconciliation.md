<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
SPDX-License-Identifier: MIT
-->

# Application identity and settings reconciliation

Crossplane manages directory entries and typed application identities. Flux owns
those resources, application settings and deployment order. The LDAP provider uses
Upjet and the Terraform provider SDK; there is no Terraform CLI or state directory.
The LDAP foundation is reconciled by the same Upjet provider. OpenTofu has
been retired; see the [migration and recovery evidence](opentofu-to-crossplane-migration.md).

## Jellyfin identity

The application namespace opts in to the cluster-owned `directory` backend. Its
central LDAP administrator credential stays in `identity/openldap`; application
credentials stay in `media/jellyfin-identity`. A central-provider Entry must be a
direct child of an allowed base, with an RDN prefixed by its namespace and a dot.
The generic Entry API reconciles only explicitly owned attributes. Jellyfin's
[GroupOfNames declarations](../kubernetes/apps/media/jellyfin/groups/groups.yaml)
live alongside its application manifests. They reconcile in `identity` using the
shared `directory-admin` ProviderConfig, preserving the existing group DNs without
relaxing the media namespace's provider boundary. The generic directory platform
contains no Jellyfin group definitions or health checks.

For new groups, `initialMembers` seeds `admin`, `sofie` and `lcp` in the user group,
and `admin` in the admin group. Existing memberships and owners are preserved.
Subsequent changes belong in Canaille. Either
group permits sign-in; only `jellyfin.admin` grants Jellyfin administrator privileges.
The LDAP plugin updates that privilege on each successful login. The former
`media.jellyfin-users` group and its managed Entry were removed after verifying all
three members were preserved in `jellyfin.user` and the new roles worked live.

Jellyfin members sign in with their LDAP username or email address and password.
Both lookup attributes resolve to the same `uid` account identity. Keep email
addresses unique among members and distinct from other members' usernames; the
upstream plugin chooses the first result when aliases collide. The
unmodified upstream LDAP plugin links the account on login and synchronizes its
`jpegPhoto` through its existing scheduled task. Jellyfin does not need a separate
OIDC client or an SSO plugin for this path. The local `ops-recovery` administrator
remains available for emergency access and settings reconciliation.

LDAP password changes remain disabled. Password management happens directly in
Canaille. The upstream `PasswordResetUrl` is cleared: this web client puts the URL
into a local PIN-reset dialog and then asks for a PIN, rather than providing a
usable external reset flow. No extra web menu link or custom web configuration
is installed.

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
uses its bind-account Entry; application groups are managed centrally in the identity
namespace. Deleting an Entry deliberately
retains the directory object; permanent deletion is an explicit administration action.

The directory foundation also uses the existing Upjet provider. Its 14 entries
are adopted by DN; passwords, profiles and human membership are create-only
defaults. OpenTofu and its state/controller are retired. Grafana uses the
`OAuth2Client` composition with PKCE, signed tokens and strict administrator-group
access, while retaining its local recovery login. See
[directory recovery](opentofu-to-crossplane-migration.md) and
[Grafana sign-in](../kubernetes/apps/monitoring/victoria-metrics/identity/README.md).

### Group adoption evidence

On 2026-10-17, both Jellyfin groups were adopted by app-owned `GroupOfNames`
resources. The old Entries first became Observe-only, then finalized after the
new composed Entries were Ready/Synced. The same LDAP UUIDs and canonical member
and owner hashes were verified before and after adoption. Only then were the
unused `jellyfin-groups` ProviderConfig and attribute Secret removed. The real
kind/Helm regression also verified creation, drift repair, retained LDAP groups
and membership/owner preservation through re-adoption.

## Application settings

Jellyfin's application-local Crossplane resources declare plugin settings, login branding,
network settings and library paths. The namespaced Upjet provider embeds the pinned
Jellyfin Terraform provider and repairs API drift. It replaces the configuration
sidecar while preserving the application's current storage policy.
The LDAP plugin configuration owns only declared keys, preserving user mappings,
photo hashes and other runtime state. Credentials stay in namespace-local Secrets.
The ProviderConfig can read the existing administrator username from a Secret key; no
separate automation account is required. See the
[provider migration and build instructions](../packages/provider-jellyfin/README.md).
The upstream Jellyfin Terraform provider retains its MPL license.

New authored integration code is MIT. Generated Upjet CRDs keep their upstream
license through generated sidecars. Upstream Jellyfin/plugin binaries retain their
own licenses in the image. Regenerate CRDs using the provider README; do not edit
generated files to add licensing text.

## Acceptance tests

```sh
# Wait for adopted Jellyfin settings and libraries to reconcile.
kubectl -n media wait --for=condition=Ready --timeout=180s \
  pluginconfigurations,systemconfigurations,networkingconfigurations,brandingconfigurations,libraries --all
kubectl -n media wait --for=condition=Synced --timeout=180s \
  pluginconfigurations,systemconfigurations,networkingconfigurations,brandingconfigurations,libraries --all
# Read-only provider/API smoke checks through the chart's Helm hook.
task app:test NAMESPACE=identity RELEASE=ldap-provider
# Verify final Flux rendering, including the declared storage policy.
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
under `/config/server` use a separate persistent volume. Configuration and media
backups and automatic restore dependencies were removed by operator request.
Crossplane reconstructs declared settings; it cannot reconstruct lost user mappings,
photos or application databases. The historical recovery evidence below describes
the former backup deployment, not an active recovery guarantee. See the
[current storage policy](../packages/jellyfin-runtime/README.md) and
[backup cleanup](jellyfin-backup-cleanup.md).

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

### Jellyfin 12.2 upgrade recovery

On 2026-10-17, source `22732d6891984d078455a98be1ae4b06e57a0ac2` published
Jellyfin 12.2 with upstream LDAP 24 as
`ghcr.io/soupglasses/ops/jellyfin-runtime@sha256:6947130a86a85e82a545c0206cde9740d9e24374b137d35037ae3cc2dbb75200`.
Jellyfin 12 disables legacy token headers by default; the settings client now puts
its session token in the supported `Authorization: MediaBrowser` header.
Real-image LDAP login, photo synchronization and administrator creation, promotion
and demotion passed before publication.

Before production migration, an offline checkpoint was backed up off-cluster as
snapshot `b4b1ef9b` at 19:31:19 UTC. A separate destination restored that exact
snapshot at 19:31:53 UTC. The restore helper validated all 41 files in generation
`aa1e11e5b58143df923230cb8a5956e7`. An isolated PVC ran the new image and retained
the server identity, all three original account IDs and administrator flags, all
three libraries and their paths, and the baseline media counts. LDAP 24 loaded
Active and the authenticated settings comparison passed. After clean shutdown,
all 41 files and SQLite integrity for `data/jellyfin.db` passed verification.

For rollback across this database migration, restore `b4b1ef9b` into separate
storage and use the matching preupgrade image:
`ghcr.io/soupglasses/ops/jellyfin-runtime@sha256:ae736303e6c1ed9ca00824356042a1335e9ce98403b21934faf4e296b02a0a30`.
Never start the older image against the migrated database. Directory snapshot
`56827bc8` preserves the pre-migration group membership. Existing off-cluster
retention, media recovery and scheduled destination refresh remain unchanged.

Production deployed the new digest through Flux. A disposable directory identity
verified administrator API access, demotion to an ordinary user with administrator
API denial, and sign-in denial after all application memberships were removed.
The fixture and its Jellyfin account/link were deleted. The permanent `admin`
account uses the LDAP authentication provider and administrator policy; its normal
LDAP password remains authoritative. Existing user accounts were preserved. A
library refresh was triggered on the upgraded server. All isolated migration
resources were removed. Independent security and resilience reviews approved the
change; evidence is recorded in [issue 107](https://github.com/soupglasses/ops/issues/107).

### Jellyfin Upjet checkpoint compatibility

The Upjet migration retains the same Jellyfin 12.2 and LDAP 24 binaries and the
offline checkpoint format. A real-image fixture now configures the server through
the generated Upjet connector, verifies LDAP login by username and email, checks
administrator promotion/demotion and profile-photo synchronization, and repairs
owned-setting drift without replacing LDAP user mappings or library identities.

The fixture exports an offline checkpoint and restores it into a second isolated
Jellyfin instance. Server identity, library IDs, LDAP mappings and photo bytes remain
unchanged. Fresh provider processes adopt the restored state, correct a deliberately
changed LDAP host, pass an unchanged-state comparison, and authenticate the same LDAP
user with the expected administrator role. All disposable containers are removed.

This tests application-state compatibility with the new API writer and the retained
checkpoint utilities. Production backups and restore dependencies were separately
removed by operator request; this fixture does not establish current off-cluster
recoverability. The native kind test additionally applies a namespaced resource
through the installed controller, verifying observation without writes, desired
updates and drift correction while preserving users and unspecified settings.
Rollout and package evidence is tracked in
[issue 110](https://github.com/soupglasses/ops/issues/110).

### Jellyfin native provider rollout

On 2026-10-18, the native Crossplane 2.4.2 package replaced the settings sidecar.
The provider embeds the pinned Terraform provider ownership fix and exposes
namespaced APIs; settings remain beside the Jellyfin app manifests. It reuses the
existing `ops-recovery` administrator. No additional account or secret was created.

The corrected package is
`ghcr.io/soupglasses/ops/provider-jellyfin@sha256:df798c6aedf296135b3239666236ab8f33c2157909dceb90a003b11682fa95f0`,
built from `b34cb7b705eb87f6317c40f0811e4c82275896a7`.
[Publication CI](https://github.com/soupglasses/ops/actions/runs/39091069770)
passed generated-schema reproduction, connector tests, native installation under
restricted Pod Security, real controller reconciliation in kind, and composition
rendering with synthetic credentials. The controller observed settings without
writes, applied desired values, corrected drift, and preserved existing users.
The disposable kind cluster was deleted.

Live adoption first passed strict Ready and Synced checks for all expected settings,
libraries and the LDAP composition under Observe-only policies. After upgrading the
provider, the temporary Secret-selector migration reference was removed and the same
checks passed again at `1c878626efd2f060e295eaac4720173900c1fd6d`.
The sidecar retired at `25259821d937b6acde176cf594c5536691563b97`; the replacement
single-container pod became healthy with unchanged server identity, version and
completed setup. Its desired-settings and credential mounts are absent.

`bba29dd84757bb4eb5b31e43b1fdc8a78e07dfff` enabled Observe/Create/Update after
retirement. Delete remains excluded. Flux's strict settings health checks passed
under the new writer at `2db8596c10f17fc5f91f971167cfcade4079ddbb`, after correcting
the temporary legacy ConfigMap's namespace. Private HTTPS remained healthy.
Independent security and
resilience reviews approved this staged sequence. Existing PVCs and the current
storage policy were preserved. Plugin settings now use Crossplane; binary plugin
installation remains image-seeded until its replacement and restart lifecycle is
separately proven.

The unused legacy ConfigMap is adopted into the provider's pruning inventory;
its removal is the remaining cleanup. Resume from the
[handoff document](jellyfin-upjet-handoff.md).
