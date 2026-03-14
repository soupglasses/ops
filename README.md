<!--
SPDX-FileCopyrightText: 2025 Sofie <sofie+git@mailbox.org>

SPDX-License-Identifier: MIT
-->

# Home Operations

Just another home operations Talos Linux cluster.

## Overview

Kubernetes cluster is deployed on top of Talos Linux, bootstrapped with
Talos' inline manifests. Applications are deployed using FluxCD.

### Network layout

Mikrotik RB5009 Router handling forwarded connections from a VPS through Wireguard.

Currently only doing simple port-forwarding. A BGP based load balancer is
planned for the future to automate this process.

## Tasks

All operations are managed through [Task](https://taskfile.dev/). Tasks are
organized into namespaced groups under `.taskfiles/`:

```bash
task --list
```

### Talos

| Task | Description |
|---|---|
| `talos:bootstrap` | Bootstrap kubernetes cluster (first time only) |
| `talos:rebootstrap` | Rebootstrap cluster from control plane node by wiping STATE and EPHEMERAL |
| `talos:reset` | Permanently reset a Talos node |
| `talos:instantiate` | Apply config to a node |
| `talos:genconfig` | Generate talos configurations |
| `talos:kubeconfig` | Generate talos kubeconfig |

### Flux

| Task | Description |
|---|---|
| `flux:bootstrap` | Bootstrap FluxCD on the cluster |
| `flux:reconcile` | Trigger manual reconcile for Flux |
| `flux:reconcile-ks` | Reconcile all Flux Kustomizations |

### Kubernetes

| Task | Description |
|---|---|
| `kube:clean-pods` | Delete all Failed/Pending/Succeeded pods |
| `kube:fetch-agekey` | Fetch age key from the cluster |
| `talos:shell` | Spawn a temporary root shell on a node |

### License

| Task | Description |
|---|---|
| `license:mit` | License files as MIT |
| `license:0bsd` | License files as 0BSD |

## Configure new cluster

1. **Install Talos Linux** on each control-plane and worker node. Generate `.secure/kubeconfig`, `.secure/talosconfig` using [Talos: Production Notes](https://www.talos.dev/v1.10/introduction/prodnotes/). After install, put each created machine patch into the `talos/` folder for safekeeping. You should also use Talos bootstrap script to set up the kubernetes cluster.
2. **Install FluxCD** following this guide [Flux: Gitea bootstrap](https://fluxcd.io/flux/installation/bootstrap/gitea/). Remember to set `--hostname=codeberg.org` and place the configuration into `kubernetes/clusters/$CLUSTER_NAME`.
3. **Configure the SOPS encryption** using [Flux: Encrypting secrets using age](https://fluxcd.io/flux/guides/mozilla-sops/#encrypting-secrets-using-age). Place the generated key into `.secure/age.agekey`. Ensure you also add the public key into `.sops.yaml` as well.

## Adding worker nodes

1. Add the worker to `talos/talconfig.yaml` with `controlPlane: false`.
2. `task talos:genconfig` to regenerate configs.
3. Install Talos on the worker, then apply its config:

   ```bash
   task talos:instantiate NODE=<worker-ip> NAME=<hostname>
   ```

   The worker joins the cluster automatically via the configured endpoint.

## Recovery

### Corrupted EPHEMERAL partition

After a power outage the XFS filesystem on the STATE and EPHEMERAL partitions
may be corrupted. This prevents etcd, kubelet, and trustd from starting,
leaving the cluster stuck in `booting` stage with `:6443` refusing connections.

Symptoms:

- `talosctl services` shows no etcd, kubelet, or trustd
- `talosctl get volumestatus` shows STATE or EPHEMERAL in `failed` state
- `talosctl dmesg` shows XFS CRC / metadata I/O errors on the STATE or
  EPHEMERAL partition

`talos:rebootstrap` rebuilds the entire cluster starting from the control
plane node:

```bash
task talos:rebootstrap NODE=172.21.69.10
```

This wipes both STATE and EPHEMERAL (we no longer trust the disk), bootstraps
a fresh etcd cluster, waits for the node to become healthy, and re-bootstraps
FluxCD to reconcile all applications from git. Persistent data on separate
drives (e.g. ZFS pools) is not affected.

If worker nodes also have corrupted STATE or EPHEMERAL partitions, reset them
individually after the control plane is back with `talos:reset`. Workers
rejoin automatically once they can reach the API server.
