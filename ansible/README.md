<!--
SPDX-FileCopyrightText: 2026 Soup Bot <bot+ops@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# RKE2 host management

This tree owns the Ubuntu 26.04 host beneath Flux: the automation identity, native
systemd-networkd configuration, CIS-derived OS baseline, non-destructive ZFS adoption,
NVIDIA prerequisites, and pinned single-server RKE2. Flux continues to own workloads and
post-bootstrap Cilium policy.

RKE2 runs its `cis` profile. The host applies a default-deny input firewall, AIDE, audit retention,
password policy, automatic security updates, SSH controls, and kernel hardening. Kubernetes requires
documented forwarding, eBPF, namespace, overlayfs, and ZFS exceptions. Ubuntu 26.04 is not claimed as
formally CIS certified until Canonical publishes a matching USG profile; the implemented and deferred
controls are tracked in `docs/cis-control-mapping.md`.

## Safety boundaries

- The ZFS role never creates, destroys, exports, rolls back, or mounts the existing pool or its old
  datasets. It imports `zfspool` with `-N`, keeps it out of the generic import cache, and prints the
  dataset inventory. A dedicated systemd unit repeats that non-mounting import after reboot.
- `zfspool/k8s` is created only with `zfs_create_dataset=true`. Existing datasets remain a temporary
  safety net; recover applications from off-cluster S3 backups into the new dataset and prove their
  contents before considering any pool recreation.
- Reformatting to RAIDZ2 requires a separate resilience review, successful application-level S3
  restore drills, an independently preserved rollback point, and explicit destructive approval.
  There is deliberately no destructive ZFS task here.
- The RKE2 token and backup credentials are secrets. Supply them from a new SOPS-encrypted vars file
  maintained by a human; never commit plaintext. The role does not print the supplied token.
- The first network change can interrupt SSH. Keep console access available. The play waits for
  `172.21.71.10` and switches the remaining connection to it.

## Install tooling

Use a Python virtual environment, then install the pinned controller and collections:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/ansible-galaxy collection install -r requirements.yml
```

Run commands from this directory so `ansible.cfg` is discovered.

## Bootstrap and converge

The committed inventory targets the final address. First create the automation account using the
temporary installer address and the public half of its dedicated SSH key:

```bash
ansible-playbook playbooks/bootstrap.yml \
  -u sofie -e ansible_host=172.21.71.254 \
  -e '{"automation_authorized_key":"ssh-ed25519 AAAA... ansible@controller"}'
```

The inventory pins SSH host-key verification to `nas.home.arpa`, whose key must already be present in
the controller's `known_hosts`. This preserves strict checking across the `.254` to `.10` transition;
verify the key out of band before adding it on a new controller.

Confirm console access. Then converge the host. On the first run, override the address so Ansible can
perform the network handoff:

```bash
ansible-playbook playbooks/site.yml \
  -u ansible -e ansible_host=172.21.71.254 \
  -e host_firewall_audit_mode=true \
  -e '{"automation_authorized_key":"ssh-ed25519 AAAA... ansible@controller"}'
```

The first run keeps the Cilium host endpoint in temporary audit mode. Exercise a new SSH session,
API, monitoring, BGP, backup, IPv4, and IPv6 paths and inspect Hubble policy verdicts. Then rerun
without the override to enforce the host policy. TCP/22 remains allowed from the internal IPv4
space and the owned IPv6 prefix; keep console access available for the transition.

Later runs use the committed `172.21.71.10` inventory directly. After reviewing the old datasets,
create only the new OpenEBS parent when authorized:

```bash
ansible-playbook playbooks/site.yml \
  -e zfs_create_dataset=true \
  -e '{"automation_authorized_key":"ssh-ed25519 AAAA... ansible@controller"}'
```

Before enabling GPU workloads, run `ubuntu-drivers list --gpgpu` on the node and set
`nvidia_driver_package` to an exact package from that output. Driver installation is deliberately
not guessed. Install the matching NVIDIA container toolkit repository/package only after validating
its Ubuntu 26.04 support; Kubernetes device-plugin resources remain Flux-owned.

## Configuration and secrets

Non-secret defaults live in `inventories/home/group_vars/all/main.yml`. Copy
`secrets.example.yaml` to `secrets.sops.yaml`, replace every placeholder, and run exactly:

```bash
sops --encrypt --in-place inventories/home/group_vars/all/secrets.sops.yaml
```

The pinned `community.sops` vars plugin decrypts it in memory on the controller. Never pass secrets
with `-e`, which exposes them to shell history and process listings. Follow the repository's SOPS
rules; existing encrypted files must never be edited or decrypted by automation agents.
Keep the example's `stringData` wrapper: it matches the repository's SOPS encryption rule.

The role refuses to start RKE2 without a stable token and complete S3 etcd settings from that file.
Local compressed snapshots run every six hours; S3 adds the independent copy needed for clean-host
recovery. Preserve the token separately because snapshot restore requires the same token used to
encrypt bootstrap data.

RKE2 is pinned to `v1.34.11+rke2r1` and configured at cluster creation with dual-stack pod and service
CIDRs. Its bootstrap Cilium values enable native routing, kube-proxy replacement, dual stack, BPF
masquerading, BGP control plane, Maglev, bandwidth manager/BBR, Envoy, Hubble, metrics, and the host
firewall. DSR, transparent encryption, and ServiceMonitors stay off until their dependencies and
traffic paths have been proven. Ansible owns the day-zero host policy; Flux owns the Cilium BGP
peers, advertisements, LB-IPAM pools, and workload policies.

## Verification and maintenance

```bash
task test
task check
task apply
task verify
```

`verify.yml` checks networkd, auditd, RKE2, Kubernetes node readiness, both node address families,
Cilium rollout, and ZFS pool health through their user-visible interfaces. Continue with Cilium's
connectivity test, BGP route/withdrawal checks on RouterOS, S3 application restore drills, and the RKE2
CIS benchmark before declaring recovery complete.

For upgrades, select a pinned `rke2_version` and run `playbooks/upgrade-rke2.yml`. It takes a local
etcd snapshot before changing RKE2. Confirm a fresh independent backup and saved server token first;
upgrade one Kubernetes minor at a time and run the full verification afterward.

## RouterOS

Bootstrap the dedicated RouterOS identity once through the existing administrator,
then use the committed `ansible` inventory identity:

```bash
ansible-playbook playbooks/bootstrap-router.yml -u 'ansible+cet1024w' \
  -e routeros_automation_public_key_file="$HOME/.ssh/ansible.pub"
ansible-playbook playbooks/router.yml
ansible-playbook playbooks/verify-router.yml
```

The bootstrap creates the account disabled, installs its key, and enables it last. The
RouterOS permission model cannot scope `write` rights to BGP/firewall objects, so the
role limits its own changes by unique comments and exact selectors.
