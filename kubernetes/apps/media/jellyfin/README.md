<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
SPDX-License-Identifier: MIT
-->

# Jellyfin

`app/` contains the deployment, storage, networking and configuration.
Edit `app/settings.yaml` for server settings, libraries and LDAP plugin settings.
`app/identity.yaml` declares namespace-local `ops.finnes.dev` ServiceAccount and
access groups.
Crossplane generates the bind password and supplies it to the LDAP plugin only
after LDAP confirms it. Change the ServiceAccount's `rotationID` to rotate it;
the Jellyfin recovery administrator still uses its encrypted Secret. Manage human
membership and ownership in Canaille.

One Flux Kustomization installs the app after its infrastructure dependencies and
checks the Deployment. Crossplane independently reconciles the inline settings;
connection failures while Jellyfin starts are retried by its controller.

The provider installation and reusable plugin settings API live with the
[Crossplane platform](../../crossplane-system/crossplane/providers/jellyfin).
See the [runtime policy](../../../../packages/jellyfin-runtime/README.md) for
plugin updates and the intentional absence of configuration and media backups.
