<!--
SPDX-FileCopyrightText: 2025 Sofie <sofie+git@mailbox.org>

SPDX-License-Identifier: MIT
-->

# Home Operations

Home infrastructure and Kubernetes operations.

## Overview

The Kubernetes cluster runs RKE2 on an Ansible-managed Ubuntu host. Ansible owns the
physical host, native systemd-networkd configuration, CIS-oriented baseline, ZFS,
RKE2, and its day-zero Cilium configuration. Flux owns applications and post-bootstrap
cluster policy.

The former Talos configuration is retained temporarily as a rollback and migration
reference. Remove it only after the documented S3 restore drills pass on the final
storage layout.

### Network layout

Mikrotik RB5009 Router handling forwarded connections from a VPS through Wireguard.

Load balancing is BGP based: Cilium's BGP control plane peers with the router and
announces the LoadBalancer IP, which the router DNATs public traffic to. Details in
`docs/network-topology.md` and `docs/routeros-bgp-config.md`.

## Tasks

All operations are managed through [Task](https://taskfile.dev/). Tasks are
organized into namespaced groups under `.taskfiles/`:

```bash
task --list
```

### Host and RKE2

| Task | Description |
|---|---|
| `ansible:deps` | Install pinned Ansible collections |
| `ansible:test` | Run Ansible syntax and lint checks |
| `ansible:check` | Preview host changes |
| `ansible:apply` | Converge Ubuntu and RKE2 |
| `ansible:verify` | Verify host, Kubernetes, Cilium, and ZFS health |

See `ansible/README.md` for the initial `.254` to `.10` network handoff and
`docs/rke2-operations.md` for routine operations.

### Flux

| Task | Description |
|---|---|
| `flux:bootstrap` | Bootstrap FluxCD on the cluster |
| `flux:reconcile` | Trigger manual reconcile for Flux |
| `flux:reconcile-ks` | Reconcile all Flux Kustomizations |

### Flux Local Validation

Validate Flux configurations locally before committing:

| Task | Description |
|---|---|
| `flux-local:test` | Validate all Kustomizations and HelmReleases |
| `flux-local:diff` | Show diff of rendered manifests against HEAD |
| `flux-local:build` | Build all Kustomizations and count resources |
| `flux-local:get-ks` | List all Kustomizations |
| `flux-local:get-hr` | List all HelmReleases |

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

## Getting started on a new controller

Install the mise tools and Ansible dependencies, then supply the existing SOPS age
identity and kubeconfig through `.secure/` using an independent recovery copy:

```bash
mise install
cd ansible
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/ansible-galaxy collection install -r requirements.yml
```

Never treat the cluster as the only copy of its age identity, RKE2 token, restic
password, or S3 credentials.

## Recover the cluster

1. Install Ubuntu 26.04 LTS with OpenSSH and temporary passwordless sudo access.
2. Bootstrap and converge the host using `ansible/README.md`.
3. Install FluxCD with `task flux:bootstrap`. The recovery task uses generic Git
   bootstrap over HTTPS so the `soupbot` collaborator does not need repository
   administration access. It places the configuration in
   `kubernetes/clusters/$CLUSTER_NAME`.
4. Restore fresh PVCs from S3 and validate application contents as described in
   `docs/zfs-s3-recovery.md`.

## Recovery

The recovery contract is this repository, independently held recovery keys, and the
off-cluster S3 backups. A successful backup job is not proof: periodically restore to
new storage and validate application-level contents. See `docs/zfs-s3-recovery.md`.
