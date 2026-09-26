<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
SPDX-License-Identifier: MIT
-->

# Hermes Kubernetes access

Hermes profiles use individual ServiceAccounts in `hermes-system`. Their credentials
expire, and must be replaced after a cluster rebuild changes the Kubernetes CA or
ServiceAccounts.

From this repository, with verified SSH access to `nas` through the Ansible key and
to the destination host, deploy access to its existing `hermes` container:

```bash
task hermes:deploy-keys HOST=satellite
```

`HOST` is an SSH host or alias. SSH uses your normal configuration and keys. The
host must allow `sudo -n podman`, with a running container named `hermes`, its
`hermes` user, and all six existing `/opt/data/profiles/<profile>` directories.
This task configures access in that container. It does not provision a host or container.

The task requests 30-day credentials, fetches the current public cluster CA, and sends
them over SSH to the `hermes` container. It validates each credential's identity and
pod-read permission before atomically replacing that profile's kubeconfig. Credentials
travel through process input, never command arguments or terminal output. Existing
files remain in place if verification fails. The task also sets each profile’s
`KUBECONFIG` and `HERMES_KUBECONFIG` paths through Hermes’ native configuration
setters and preserves existing terminal environment passthrough settings. Gateway-level
path variables point to `/dev/null` until a profile supplies its own scoped path.
After first configuring these settings, restart Hermes when its active work has
finished. Subsequent credential rotations replace files at the same paths. Profiles completed before a later failure
remain rotated, so rerunning the task is safe.

The output lists each identity and its actual server-issued expiry. Schedule the next
rotation before that expiry by running the same task again.
To rotate one profile or request a different lifetime:

```bash
task hermes:deploy-keys HOST=satellite -- --profile observability --duration-seconds 604800
```

Profile kubeconfigs live at `/opt/data/profiles/<profile>/kubeconfig.yaml`, backed by
`/var/lib/hermes/data` on satellite. Files are owned by the container's `hermes` user
and readable only by that user. The container must have Python and the repository's
Mise available. The task selects `kubectl` using this repository's `.mise.toml` version
without loading the runtime checkout's configuration.

Access is defined in `kubernetes/apps/flux-system/hermes-access/app/rbac.yaml`.
All profiles observe workloads and operational resources without Secret, exec, or
token-issuance access. The security profile additionally reads RBAC and admission
configuration. The implementer additionally patches existing Flux resources for
reconciliation. Rotation changes credentials, not these permissions.

Rotation does not revoke earlier tokens that are still valid. For a compromised
profile, delete only its ServiceAccount in `hermes-system`, reconcile the
`hermes-access` Flux Kustomization to recreate it, then rotate that profile. The new
ServiceAccount UID invalidates its earlier tokens. See Kubernetes’
[ServiceAccount token revocation guidance](https://kubernetes.io/docs/reference/access-authn-authz/service-accounts-admin/#deleteinvalidate-a-short-lived-serviceaccount-token).

After first-time setup, restart the systemd-managed container when active work has
finished so its gateway loads the new settings:

```bash
ssh satellite 'sudo -n systemctl restart hermes.service'
```

Routine key replacement uses the existing paths and needs no restart.
