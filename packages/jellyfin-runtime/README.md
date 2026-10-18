<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: MIT
-->

# Jellyfin runtime and recovery

The image supervises Jellyfin and installs the pinned upstream LDAP plugin. Application
configuration lives in `/config/server`; `/cache` is disposable. Media is a separate
read-write mount for media and sidecar metadata. Neither volume is backed up.

## Storage policy

Jellyfin configuration and media have no backups by operator choice. The deployment
preserves live configuration and media volumes and starts directly without restore
init containers. A new configuration PVC starts an empty installation. Media recovery
depends on surviving NAS storage or obtaining the files again.

The runtime image still contains checkpoint and restore utilities, but the deployment
has no checkpoint volume, quiesce controller, VolSync source or destination, or restic
credentials. These utilities are inactive in the deployed configuration. The supervisor
provides process lifecycle management and the liveness control socket; `health.py`
checks the Jellyfin HTTP health endpoint for readiness.

See [backup cleanup](../../docs/jellyfin-backup-cleanup.md) for removing old backup
history and controllers. Retain the image digest, plugin locks, source references and
Dockerfile so the runtime can be rebuilt if its published image becomes unavailable.

## Configuration ownership

The namespaced Upjet provider owns the API settings and libraries declared in
`kubernetes/apps/media/jellyfin/settings/`. The application has no
configuration sidecar. The runtime retains checkpoint, restore, health checks,
and the pinned LDAP plugin distribution. See
[provider migration](../provider-jellyfin/README.md) for adoption and
recovery ordering.

## Tests

```sh
python3 -m unittest discover -s packages/jellyfin-runtime/tests -v
```

The tests exercise real child processes and the Unix control socket, checkpoint
failure preservation, corrupt or unsafe archives, first-install guards and restoration
of database records, plugin configuration and images. They need no cluster or secrets.

The opt-in Docker/OpenLDAP fixture also runs the generated Upjet connector against
real Jellyfin, then restores its checkpoint and verifies re-adoption preserves
identities, LDAP mappings and photos. Set `JELLYFIN_INTEGRATION_IMAGE` to the built
runtime image and `JELLYFIN_PROVIDER_TEST_IMAGE` to the provider Dockerfile's `tests`
target before running `test_identity_integration.py`. All credentials in that
fixture are synthetic.
