<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
SPDX-License-Identifier: MIT
-->

# Jellyfin Upjet provider

This controller embeds the Jellyfin Terraform Plugin Framework provider through
Upjet. It runs no Terraform executable and needs no Terraform state volume.
The source is pinned to
[the ownership fix](https://github.com/soupglasses/terraform-provider-jellyfin/commit/65dbe330d0862501b43c841ab7ed93953096ad3a).

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
The kind test installs the native package and a disposable Jellyfin Pod, then applies
namespaced resources through Kubernetes. It verifies observation without writes,
controller-driven settings updates and drift correction while preserving user identities.

## Configuration and adoption

Crossplane installs the native OCI provider package declared in
`kubernetes/apps/media/jellyfin/provider/provider.yaml`. Its runtime config runs
the controller in `crossplane-system`, watching only `media`. Crossplane manages provider revisions
and the generated CRDs; the package embeds its controller image for recovery.

Crossplane 2.4.2 also grants native provider controllers cluster-wide Secret
read/write permissions through its generated system ClusterRole. Namespace-scoped reconciliation and local
credential selectors therefore do not establish a Kubernetes authorization boundary.
This deployment accepts that standard trusted-controller authority; it does not
use an external runtime. The isolated native-package test verifies the effective
permissions and the namespace scope of its APIs. See the
[upstream baseline rules](https://github.com/crossplane/crossplane/blob/v2.4.2/internal/controller/rbac/provider/roles/roles.go#L73).

Application settings live in `kubernetes/apps/media/jellyfin/settings/`.
The ProviderConfig selects the existing `ops-recovery` first-setup administrator.
Other deployments can set `username` directly, or `usernameSecretRef` to a local
Secret key such as `JELLYFIN_ADMIN_USERNAME`; no additional account is required.
The PluginSettings composition reads local Secret references, serializes the
configuration safely into a separate Secret, and composes the ordinary generated
PluginConfiguration. It reuses the existing function-go-templating installation.
No encrypted source file needs editing or decryption, and credentials do not pass
through Helm release storage.

Start adoption with `[Observe]`, preserving the existing sidecar. Confirm every
managed resource resolves the existing external object, then remove the sidecar
and enable `[Observe, Create, Update]`. Never run both writers during the handoff.
The resulting manifests exclude Delete: pruning cannot remove libraries, plugins
or application data. Libraries own their declared paths; the upstream provider
requires replacement for path changes, so those changes need a deliberate migration.

The manifest pins the native package by digest. The publication workflow tags each
package with its source revision so the controller can be rebuilt from this repository.

The current application policy has no configuration or media backups and no restore
init containers, by operator request. Preserve the existing PVCs during adoption.
Library names and plugin GUIDs identify existing objects; user mappings, photos,
memberships and other unmanaged settings remain application data. Crossplane cannot
reconstruct that data after its loss. The real-image checkpoint fixture tests the
retained utilities and provider compatibility, not an active production backup path.
See the [runtime storage policy](../jellyfin-runtime/README.md).

The runtime still supplies the pinned LDAP binaries. Although this provider
generates Plugin, PluginRepository and ScheduledTask APIs, production plugin
installation is not handed over yet. That handoff must remove image seeding,
establish repository and schedule ordering, load installations through a restart,
and prove a clean restore. Do not add a competing Plugin version declaration
while the image overwrites plugins at startup.
