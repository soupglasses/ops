<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: MIT
-->

# Jellyfin

Flux installs the provider, reconciles LDAP identities, starts the application,
and applies API settings after the server is available. `ks.yaml` declares those
four stages; application settings do not require editing provider internals.

- `app/`: server, storage, networking and storage alerts.
- `identity/`: bind identity in `media` and access groups in `identity`.
- `settings/`: server settings, libraries and LDAP plugin configuration.
- `provider/`: Crossplane provider installation and the plugin settings API.

Manage human group membership and ownership in Canaille. Git declares groups
without granting human access. Keep the platform sentinel in otherwise empty
groups; see the [LDAP chart](../../../../charts/ldap-provider/README.md).

The [runtime image](../../../../packages/jellyfin-runtime/README.md) contains the
pinned LDAP plugin and a launcher that runs Jellyfin directly. Configuration and
media are deliberately not backed up; preserve existing PVCs during changes.
Kubernetes checks the server's native health endpoint for liveness. Startup and
readiness additionally require its response to say `Healthy`.
