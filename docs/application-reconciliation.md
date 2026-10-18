<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
SPDX-License-Identifier: MIT
-->

# Application identity and settings

Flux owns application declarations and deployment ordering. Crossplane reconciles
identity and application configuration through typed APIs. The LDAP provider uses
Crossplane's managed reconciler and a native LDAP client; the Jellyfin provider
reuses its Terraform SDK without a Terraform executable or state directory.

## Shared identity

Human identities belong to LDAP and are administered through Canaille. Prefer SSO
where supported. Applications may retain a local first-configuration or recovery
administrator, but must not maintain a separate human credential catalogue.

The directory platform owns its backend and administrative credentials. Application
namespaces opt into a permitted backend and use namespace-local credential references.
Directory resources own only declared attributes. Passwords, profiles, memberships
and other user-managed values must survive adoption and routine reconciliation.
Identity declarations use Crossplane deletion semantics: `deletionPolicy: Delete`
removes the LDAP object when its declaration is deleted; `Orphan` retains it.
The directory foundation explicitly retains its recovery resources.

The generic LDAP [Provider](https://github.com/soupglasses/provider-ldap) is a
native Crossplane package. Ops owns the identity XRDs and Compositions separately;
the provider has no Canaille schema or application-specific configuration.

The `OAuth2Client` composition maps a typed application declaration to the backend's
LDAP schema. Applications should consume that API without embedding directory schema
or reconciliation workarounds in their own configuration. See
[Grafana identity configuration](../kubernetes/apps/monitoring/victoria-metrics/identity/README.md).

Jellyfin's [GroupOfNames declarations](../kubernetes/apps/media/jellyfin/app/identity.yaml)
are application-owned and reconciled centrally. New groups contain only the
non-login platform sentinel, which is continuously ensured without replacing
human memberships. Canaille controls human membership and ownership. The user
group permits login and the administrator group grants administrative privileges.

Jellyfin accepts LDAP usernames and email aliases as the same directory identity.
Keep aliases unique: ambiguous LDAP results can select the wrong account. The
unmodified LDAP plugin creates linked application records and imports directory
profile photos through its scheduled task. Password changes belong in Canaille.

OpenLDAP server access policy belongs to
[its deployment](../kubernetes/apps/identity/openldap/app/access-policy.ldif).
Native offline configuration runs before the server starts. Generated ConfigMap
names roll the workload when policy changes, so Git changes take effect automatically.

## Application configuration

Jellyfin's [settings](../kubernetes/apps/media/jellyfin/app/settings.yaml) declare libraries,
server settings and LDAP plugin configuration. The namespaced Upjet provider uses
its ProviderConfig credentials to complete the first setup wizard when necessary,
then authenticates normally. The application container needs no bootstrap script
or administrator credential mount.

Plugin configuration owns declared keys while preserving user mappings and other
runtime state. Binary plugins are checksum-pinned in the tested container image;
Crossplane manages their settings. See the
[runtime policy](../packages/jellyfin-runtime/README.md) and
[provider README](../packages/provider-jellyfin/README.md).

## Recovery

Persistent applications normally restore missing data from independent S3 backups
before starting. Use the [VolSync component contract](../kubernetes/components/volsync/README.md)
for PVC restoration and application-consistent backups. A restore drill must verify
usable application contents. See the
[LDAP recovery pattern](openldap-volsync-migration.md) for consistency and ordering.

Jellyfin is an explicit exception: rebuilding its configuration and media is cheaper
than storing off-site backups. Surviving PVCs are preserved. On empty storage, the
pinned image supplies plugin binaries and Crossplane recreates the first administrator,
LDAP settings and library declarations. Media must survive on the NAS or be obtained
again. Lost local preferences and playback history are not reconstructed by Crossplane.

Private application routing and split DNS preserve the public issuer name and TLS
verification for internal OIDC clients without depending on WAN hairpin routing.
See [Gateway ownership](../kubernetes/apps/network/cilium-private-gateway/README.md).

## Verification

Verify fresh installation, repeated reconciliation and drift repair through the
application API. Check shared identity, privilege changes and preservation of
unmanaged data. A ready pod or successful backup job alone does not establish these
properties.

Helm releases use native test hooks. Provider READMEs describe disposable kind and
real application integration tests. Production acceptance checks must preserve
configuration and user data. Keep operational evidence in issues, not in a duplicate
inventory of image versions, commit hashes or past rollout states.
