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

The former Talos configuration remains as a historical migration reference. Current
recovery uses the [RKE2 bootstrap guide](docs/cluster-bootstrap.md).

### Network layout

The MikroTik RB5009 routes the home WAN directly to Kubernetes services. WireGuard
provides remote access; the former public VPS tunnel is retired.

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
| `rke2:bootstrap` | Create a fresh control plane and restore applications from S3 |
| `rke2:uninstall NODE=nas` | Finish and hold backups, then run RKE2's supplied node uninstaller |
| `ansible:bootstrap-flux` | Resume the Flux handoff after host setup |
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
| `kube:kubeconfig` | Fetch and verify the RKE2 administrator kubeconfig |
| `kube:clean-pods` | Delete Failed/Succeeded pods, preserving Pending restores |
| `kube:fetch-agekey` | Fetch age key from the cluster |
| `kube:shell NODE=nas` | Spawn a temporary root shell on a node |

Bootstrap and the resumed Flux handoff run pod cleanup automatically after Flux
reconciliation, using the host connection without requiring a local kubeconfig.

### Backups

Run `task volsync:replication-sync` to trigger fresh backups for all scheduled restic
sources, display progress, and wait for every backup to complete. Use `TIMEOUT=1h`
for a longer wait or `-- --namespace identity` to limit the selection. Existing
schedules remain intact. Backup completion alone does not prove restorability; validate restored application
contents before accepting recovery.

See the [VolSync guide](kubernetes/components/volsync/README.md) for details.

Uninstall holds backups and Flux reconciliation before stopping the node. If keeping
the existing cluster, verify its application storage before `task volsync:resume NODE=nas`
(select the inventory node that coordinated the hold).
A fresh bootstrap reconstructs normal schedules from Git.

### License

| Task | Description |
|---|---|
| `license:mit` | License files as MIT |
| `license:0bsd` | License files as 0BSD |
| `license:apache2` | License files as Apache-2.0 |

Pass file paths after `--`, for example `task license:apache2 -- path/to/file`.

## Getting started on a new controller

Install the mise tools and Ansible dependencies, then supply the existing SOPS age
identity and kubeconfig through `.secure/` using an independent recovery copy:

```bash
mise install
cd ansible
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/ansible-galaxy collection install -r requirements.yml
cd ..
export PATH="$PWD/ansible/.venv/bin:$PATH"
```

Never treat the cluster as the only copy of its age identity, RKE2 token, restic
password, or S3 credentials.

## Recover the cluster

Follow the [step-by-step bootstrap guide](docs/cluster-bootstrap.md) for prerequisites,
inventory, recovery keys and post-bootstrap checks.

1. Install a supported Ubuntu release with OpenSSH and temporary passwordless sudo access.
2. Review the Ansible inventory and run `task rke2:bootstrap`. It always creates a
   fresh control plane; Flux and VolSync recover applications into new volumes from
   S3. Existing server state is refused, and missing application data blocks startup.
3. Fetch access with `task kube:kubeconfig`, then watch `flux get kustomizations -A --watch`
   and `kubectl get pods -A --watch` as applications recover.
4. Validate restored application contents before removing rollback copies. The guide
   also covers resuming a failed Flux handoff and renewing Hermes access.

## Recovery

The recovery contract is this repository, independently held recovery keys, and the
off-cluster S3 backups. A successful backup job is not proof: periodically restore to
new storage and validate application-level contents. See `docs/zfs-s3-recovery.md`.
