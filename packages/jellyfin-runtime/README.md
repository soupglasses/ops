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

## Configuration logs

The configuration sidecar checks managed settings every minute. Idle polls are silent
and still refresh its health marker. Successful writes emit JSON at `info` level with
the API resource, operation and changed field paths. Nested field paths are arrays of
key names; setting values, passwords, tokens and response bodies are never logged.
Library events identify the library and paths created or added. Each changed poll ends
with operation counts (`created`, `updated`, `path_added`, `initialized`) and a total
`changed` count. Counts describe successful API writes, not individual fields.

Writes are logged immediately, so a later failure cannot hide earlier changes. Errors
use `error` level and identify the API method and endpoint when a request fails. The
reconciler preserves unmanaged settings and libraries and performs no deletions.
Use `configuration.py --check` for an explicit in-sync result without changing settings.

## Tests

```sh
python3 -m unittest discover -s packages/jellyfin-runtime/tests -v
```

The tests exercise real child processes and the Unix control socket, checkpoint
failure preservation, corrupt or unsafe archives, first-install guards and restoration
of database records, plugin configuration and images. They need no cluster or secrets.
