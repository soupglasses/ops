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

Run `mise exec -- task check` to run every pre-commit check on tracked files.
The separate Pre-commit Checks workflow runs the same task for pull requests and
pushes to main. Commit signature verification remains a local pre-push hook.
After changing the RKE2 version, run `mise exec -- task schemas:update` to align
Kubernetes editor schema comments. The checks reject stale versions.

### Host and RKE2

| Task | Description |
|---|---|
| `ansible:deps` | Install pinned Ansible collections |
| `ansible:test` | Run Ansible syntax and lint checks |
| `ansible:check` | Preview host changes |
| `ansible:apply` | Maintain Ubuntu and the pinned RKE2 version |
| `ansible:network` | Explicit network setup or maintenance |
| `ansible:upgrade` | Back up and upgrade the pinned RKE2 release |
| `ansible:verify` | Verify host, Kubernetes, Cilium, and ZFS health |

See `ansible/README.md` for the initial `.254` to `.10` network handoff and
`docs/rke2-operations.md` for routine operations.

### Flux

| Task | Description |
|---|---|
| `flux:bootstrap` | Bootstrap FluxCD on the cluster |
| `flux:reconcile` | Trigger manual reconcile for Flux |
| `flux:reconcile-ks` | Reconcile all Flux Kustomizations |

### Flux validation

Validate Flux configurations locally with [Flate](https://github.com/home-operations/flate)
before committing. Mise and CI use the same pinned release and render settings.

| Task | Description |
|---|---|
| `flate:test` | Validate all Kustomizations and HelmReleases |
| `flate:diff` | Show diff of rendered manifests against HEAD |
| `flate:build` | Render all managed manifests, excluding Secrets |
| `flate:count` | Count rendered resources by kind |
| `flate:get-ks` | List all Kustomizations |
| `flate:get-hr` | List all HelmReleases |

Use `task flate:diff BASE=origin/main` to compare against another revision, or add
`OUTPUT=html` to save a browsable diff. CI attaches both text and HTML diffs to each
pull request. Rendering includes CRDs, excludes Secret objects and uses the pinned
RKE2 Kubernetes version.

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
3. Install the committed controllers with `task flux:install`, import existing Git
   and SOPS credentials with `task flux:bootstrap:import-secrets` as the human operator, then run
   `task flux:bootstrap`. See `docs/rke2-operations.md` for staged application recovery.
4. Restore fresh PVCs from S3 and validate application contents as described in
   `docs/zfs-s3-recovery.md`.

## Recovery

The recovery contract is this repository, independently held recovery keys, and the
off-cluster S3 backups. A successful backup job is not proof: periodically restore to
new storage and validate application-level contents. See `docs/zfs-s3-recovery.md`.
