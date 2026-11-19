<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
SPDX-License-Identifier: MIT
-->

# Jellyfin

`app/` contains the deployment, storage, networking and configuration.
Edit the manifests in `app/` for server settings, libraries and LDAP plugin settings.
`app/serviceaccount.yaml` and `app/groupofnames.yaml` declare namespace-local
`ops.finnes.dev` ServiceAccount and access groups.
Crossplane generates the bind password and supplies it to the LDAP plugin only
after LDAP confirms it. Change the ServiceAccount's `rotationID` to rotate it;
the Jellyfin recovery administrator still uses its encrypted Secret. Manage human
membership and ownership in Canaille.

One Flux Kustomization installs the app after its infrastructure dependencies and
checks the Deployment. Crossplane independently reconciles the inline settings;
connection failures while Jellyfin starts are retried by its controller.

The provider installation and reusable plugin settings API live with the
[Crossplane platform](../../crossplane-system/crossplane/providers/jellyfin).

## Plugins

`app/plugin.yaml` pins each plugin to a version from Jellyfin's repository, which
`app/pluginrepository.yaml` registers on the server. The provider installs it, uninstalls other versions and restarts Jellyfin to load it.
`app/scheduledtask.yaml` removes the triggers of Jellyfin's *Update Plugins* task,
so only these resources change plugin versions. A plugin that calls an external API
on a port other than 80 or 443 needs an egress rule in `app/ciliumnetworkpolicy.yaml`.

## Storage

The deployment runs the official image with application data in `/config/server`
on the `jellyfin` PVC; `/cache` is disposable. Media is a separate read-write NFS
volume holding the library and its sidecar metadata. Neither volume is backed up,
because rebuilding them costs less than storing them off-site: a new configuration
PVC starts an empty installation that Crossplane configures again, and media
recovery depends on surviving NAS storage. See
[backup cleanup](../../../../docs/jellyfin-backup-cleanup.md) for removing old backup
history and controllers.
