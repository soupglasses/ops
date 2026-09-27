<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# Ubuntu and RKE2 host management

Ansible provisions one Ubuntu host. RKE2 owns the control plane and bundled charts;
Flux owns applications and network policies. RouterOS is managed natively.

## Commands

Run tasks from the repository root with the pinned Python environment on `PATH`:

```bash
python3 -m venv ansible/.venv
ansible/.venv/bin/pip install -r ansible/requirements.txt
export PATH="$PWD/ansible/.venv/bin:$PATH"
task ansible:deps
task ansible:test
```

| Task | Purpose |
| --- | --- |
| `rke2:bootstrap` | Cluster-only bootstrap; running clusters are unchanged, stopped hosts prompt for OpenEBS cleanup |
| `rke2:uninstall NODE=nas` | Hold completed backups, then invoke RKE2's supplied uninstaller |
| `volsync:resume NODE=nas` | Resume a held surviving cluster after checking application storage |
| `ansible:bootstrap-flux` | Resume the Flux handoff after host setup |
| `ansible:network -- -e initial_ansible_host=172.21.71.254` | Explicit initial network handoff |
| `ansible:check` | Preview host configuration |
| `ansible:apply` | Maintain the host at its pinned RKE2 version |
| `ansible:upgrade -- -e rke2_recovery_verified=true -e rke2_upgrade_reviewed=true` | Upgrade after recovery and release review |
| `ansible:verify` | Read-only host and platform checks |
| `ansible:firewall:verify` | Fresh dual-stack connections and enforced policy checks |

Use console access for networking changes. Never override `ansible_host` during the
handoff: `initial_ansible_host` lets the play reconnect at the final address.
`nas.home.finnes.dev` must have a verified entry in the controller's SSH `known_hosts`.

Routine apply does not manage networking, router configuration or host-firewall mode.
An installed RKE2 version mismatch fails with instructions to use the upgrade task.
Changed RKE2 configuration can still restart the service; inspect check mode first.

## Credentials

Keep the existing encrypted inventory at
`inventories/home/group_vars/all/secrets.sops.yaml`. Its keys are:

- `rke2_token`;
- `cilium_bgp_ipv4_password`, `cilium_bgp_ipv6_password`.

Legacy `rke2_etcd_s3_*` keys are no longer consumed. Application backup credentials
come from the encrypted cluster configuration, independently of etcd.

`ansible:check` and `ansible:apply` enable the SOPS vars plugin and require the
existing age identity. `rke2:bootstrap` checks service state before loading credentials;
on a running cluster it exits without changes. On the cold path it explicitly loads
the encrypted host inventory, then uses protected operator-only lookups for Flux
credentials. Upgrade uses the configuration already installed on the host and explicitly
disables that plugin. Syntax/lint, network and verification do not enable
decryption. Direct operator invocations of
`site.yml` also need `ANSIBLE_VARS_ENABLED=host_group_vars,community.sops.sops`.
Never pass credentials as command arguments or print rendered secret configuration.

The host adapter maintains `cilium-bgp-ipv4-auth` and `cilium-bgp-ipv6-auth` in
`kube-system`, each with key `password`. Keep the corresponding RouterOS peer
passwords unchanged. Rotation uses the procedure in
[RouterOS operations](../docs/routeros-bgp-config.md).

Host tasks use the existing `sofie` administrator account and `~/.ssh/gpg.pub`.
The public key selects the hardware-backed SSH identity provided by the GPG agent;
keep that agent available and unlock the key when needed. The account must already
have sudo access. Ansible does not create another login or manage Sofie's keys.

On a replacement OS, establish this login through the installer or console before
running host setup. Keep its public key and recovery access independently available.

## Storage and recovery

The ZFS role imports the existing pool without mounting legacy datasets and creates
only the isolated `zfspool/k8s` parent when enabled. It never destroys a pool or adopts
old PVC datasets. Preserve those datasets until recovery is proven independently.

The GPU package remains opt-in after checking `ubuntu-drivers list --gpgpu` and
matching Ubuntu support. Applications and device plugins remain Flux-owned.

Start with the [step-by-step bootstrap guide](../docs/cluster-bootstrap.md).

See [cluster operations](../docs/rke2-operations.md) for bootstrap, maintenance,
firewall handoff and recovery. The existing OS controls are recorded in
[the CIS mapping](../docs/cis-control-mapping.md).
