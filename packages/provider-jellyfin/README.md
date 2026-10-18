<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
SPDX-License-Identifier: MIT
-->

# Jellyfin Upjet provider

This controller embeds the Jellyfin Terraform Plugin Framework provider through
Upjet. It runs no Terraform executable and needs no Terraform state volume.
The upstream fork and revision are pinned in [go.mod](go.mod).

The Go module is nested under the upstream module path because the upstream
provider constructor is internal. The `replace` directive downloads the pinned
fork; builds do not depend on another local checkout. A public upstream constructor
would let this package use the ordinary repository module path.

`config/provider.go` selects resources and stable external identifiers. Upjet
generates the APIs and reconcilers; application behavior stays in the upstream
Terraform provider. Plugin names and repository URLs remain external identifiers
even though Jellyfin returns different IDs. Only namespaced APIs are installed.
The controller watches one configured namespace and resolves credentials locally.

## Build and validation

```sh
bash packages/provider-jellyfin/build.sh /tmp/provider-jellyfin.xpkg
PROVIDER_PACKAGE=/tmp/provider-jellyfin.xpkg bash packages/provider-jellyfin/kind-test.sh
docker build --target generated --output type=local,dest=/tmp/jellyfin-generated packages/provider-jellyfin
```

Copy generated CRDs from `/tmp/jellyfin-generated/generated/crds/` into
`packages/provider-jellyfin/package/crds/`, and the generated schema into `config/schema.json`.
Do not edit generated Go files or CRDs. CI verifies reproducibility and exercises
the generated PluginConfiguration through the real Framework connector, including
drift, unmanaged values, released ownership, missing fields, controller restarts,
and non-destructive deletion. Credential tests cover rotation and local Secret
resolution. The real Jellyfin/OpenLDAP fixture uses the generated connector to
verify library identity, LDAP login, role changes, profile photos and drift repair.
The kind test installs the native package and starts an empty Jellyfin instance.
Namespaced resources create the first administrator and complete initial setup
through the provider, without a separate bootstrap script. It then verifies observation
without writes, Secret-backed plugin configuration, settings updates and drift correction
while preserving user identities.

## Configuration and adoption

Crossplane installs the native OCI provider package declared in
`kubernetes/apps/media/jellyfin/provider/provider.yaml`. Its runtime config runs
the controller in `crossplane-system`, watching only `media`. Crossplane manages provider revisions
and the generated CRDs; the package embeds its controller image for recovery.

Crossplane grants native provider controllers cluster-wide Secret
read/write permissions through its generated system ClusterRole. Namespace-scoped reconciliation and local
credential selectors therefore do not establish a Kubernetes authorization boundary.
This deployment accepts that standard trusted-controller authority; it does not
use an external runtime. The isolated native-package test verifies the effective
permissions and the namespace scope of its APIs. See the
[upstream baseline rules](https://github.com/crossplane/crossplane/blob/v2.4.2/internal/controller/rbac/provider/roles/roles.go#L73).

Application settings live in `kubernetes/apps/media/jellyfin/settings/`.
The ProviderConfig selects the existing `ops-recovery` first-setup administrator.
On an empty instance, the embedded Terraform provider uses those credentials to
create the first administrator and complete Jellyfin's setup wizard. On later
connections it authenticates without recreating accounts or resetting their passwords.
The application container needs no administrator credential or bootstrap Job.
Crossplane then reconciles the declared LDAP settings, libraries and server settings.
Other deployments can set `username` directly, or `usernameSecretRef` to a local
Secret key such as `JELLYFIN_ADMIN_USERNAME`; no additional account is required.
The PluginSettings composition reads local Secret references, serializes the
configuration safely into a separate Secret, and composes the ordinary generated
PluginConfiguration. It reuses the existing function-go-templating installation.
No encrypted source file needs editing or decryption, and credentials do not pass
through Helm release storage.

For an existing server, start adoption with `[Observe]` and confirm every resource
resolves the intended external object. Stop any previous settings writer before
enabling `[Observe, Create, Update]`. Fresh installations use those write policies
so the provider can bootstrap and configure the server automatically.
The resulting manifests exclude Delete: pruning cannot remove libraries, plugins
or application data. Libraries own their declared paths; the upstream provider
requires replacement for path changes, so those changes need a deliberate migration.

The manifest pins the native package by digest. The publication workflow tags each
package with its source revision so the controller can be rebuilt from this repository.

Jellyfin configuration and media have no backups or restore init containers: the
off-site storage cost outweighs rebuilding this data. Preserve existing PVCs during adoption.
Library names and plugin GUIDs identify existing objects; user mappings, photos,
memberships and other unmanaged settings remain application data. Crossplane cannot
reconstruct that data after its loss. The real-image checkpoint fixture tests the
retained utilities and provider compatibility, not an active production backup path.
See the [runtime storage policy](../jellyfin-runtime/README.md).

The runtime supplies pinned LDAP binaries. Keep binary updates in the tested image
and settings in Crossplane. Do not add a competing Plugin resource for an image-owned
plugin. See the [runtime plugin policy](../jellyfin-runtime/README.md#plugin-updates).
