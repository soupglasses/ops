<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: MIT
-->

# Jellyfin runtime and recovery

The image starts Jellyfin directly and installs the pinned upstream LDAP plugin. Application
configuration lives in `/config/server`; `/cache` is disposable. Media is a separate
read-write mount for media and sidecar metadata. Neither volume is backed up.

## Storage policy

Jellyfin configuration and media have no backups because rebuilding this data costs
less than storing it off-site. This is an explicit exception to automatic S3 recovery.
The deployment
preserves live configuration and media volumes and starts directly without restore
init containers. A new configuration PVC starts an empty installation. Media recovery
depends on surviving NAS storage or obtaining the files again.

The launcher copies image-owned LDAP plugin binaries into the configuration
volume, preserves plugin settings and unrelated plugins, then replaces itself
with Jellyfin. Kubernetes sends shutdown signals directly to Jellyfin. Liveness
uses the native `/health` endpoint. Readiness also checks its response body because
HTTP 200 with `Degraded` does not mean startup has completed.

See [backup cleanup](../../docs/jellyfin-backup-cleanup.md) for removing old backup
history and controllers. Retain the image digest, plugin locks, source references and
Dockerfile so the runtime can be rebuilt if its published image becomes unavailable.

## Configuration ownership

The namespaced Upjet provider owns the API settings and libraries declared in
`kubernetes/apps/media/jellyfin/app/`. The application has no
configuration sidecar. The runtime contains the plugin launcher, readiness check
and pinned LDAP plugin distribution. See
[provider migration](../provider-jellyfin/README.md) for adoption and recovery ordering.

## Plugin updates

Keep plugin binaries in the tested runtime image and plugin settings in Crossplane.
Jellyfin supports manual installation into its plugin directory. The Terraform
provider's Plugin resource can require replacement for a version change, and a
restart is needed before a newly installed plugin's configuration API is usable.
Using it here would add install/restart/configuration ordering to an already working
image rollout. Do not run both image seeding and a managed Plugin for the same binary.

`plugins.lock.json` pins the LDAP release tag and archive SHA-256. Renovate's
`github-release-attachments` datasource updates both together; its filename mapping
supports the upstream `ldap-authentication_<version>.zip` convention. Builds verify
the checksum before extraction and read the plugin GUID/version from the verified
archive's `meta.json`. There is no separate directory-version pin to forget. The MIT
license is fetched from the separately pinned `licenseRevision`.

After the real Jellyfin/LDAP integration test passes, CI publishes the source-SHA
tag and an upstream-Jellyfin-version tag derived from the Dockerfile.
Deploy the version tag with its digest: Renovate can discover a new runtime digest after a plugin
rebuild while the running deployment stays immutable. The Dockerfile's official
Jellyfin base image remains independently tag-and-digest pinned and Renovate-managed.
Plugin upgrade PRs require review and passing integration tests; future upstream
asset naming or server compatibility changes may require adjusting the build.

References: [Jellyfin plugin installation](https://jellyfin.org/docs/general/server/plugins/index.html),
[pinned provider lifecycle](https://github.com/soupglasses/terraform-provider-jellyfin/blob/65dbe330d0862501b43c841ab7ed93953096ad3a/internal/provider/plugin_resource.go),
and [Renovate release attachments](https://docs.renovatebot.com/modules/datasource/github-release-attachments/).

## Tests

```sh
python3 -m unittest discover -s packages/jellyfin-runtime/tests -v
```

The unit tests exercise plugin replacement, saved configuration preservation,
direct process handoff and readiness during degraded startup.

The opt-in Docker/OpenLDAP fixture runs the generated Upjet connector against
real Jellyfin and verifies LDAP login, photo sync, drift repair and restart
persistence. Set `JELLYFIN_INTEGRATION_IMAGE` to the built runtime image and
`JELLYFIN_PROVIDER_TEST_IMAGE` to the provider Dockerfile's `tests` target before
running `test_identity_integration.py`. All fixture credentials are synthetic.
