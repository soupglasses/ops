<!--
SPDX-FileCopyrightText: 2026 Soup Bot <bot+ops@finnes.dev>

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
| `ansible:bootstrap -- -u sofie -e initial_ansible_host=172.21.71.254` | Create the automation account |
| `ansible:network -- -e initial_ansible_host=172.21.71.254` | Explicit initial network handoff |
| `ansible:check` | Preview host configuration |
| `ansible:apply` | Maintain the host at its pinned RKE2 version |
| `ansible:upgrade -- -e rke2_recovery_verified=true` | Upgrade after verifying recovery material |
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
- `rke2_etcd_s3_endpoint`, `rke2_etcd_s3_region`, `rke2_etcd_s3_bucket`;
- `rke2_etcd_s3_access_key`, `rke2_etcd_s3_secret_key`;
- `cilium_bgp_ipv4_password`, `cilium_bgp_ipv6_password`.

Only `check`, `apply` and `upgrade` enable the SOPS vars plugin. These are operator
commands requiring access to the existing age identity. Syntax/lint, bootstrap,
network and verification do not enable decryption. Direct operator invocations of
`site.yml` also need `ANSIBLE_VARS_ENABLED=host_group_vars,community.sops.sops`.
Never pass credentials as command arguments or print rendered secret configuration.

The host adapter maintains `cilium-bgp-ipv4-auth` and `cilium-bgp-ipv6-auth` in
`kube-system`, each with key `password`. Keep the corresponding RouterOS peer
passwords unchanged. Rotation uses the procedure in
[RouterOS operations](../docs/routeros-bgp-config.md).

The controller key is `~/.ssh/ansible`; its public half supplies the managed login.
Preserve its independent recovery copy and the hardware-backed human login. Source
restrictions are in the inventory. To revoke the automation account from the console:

```bash
sudo chmod 000 /home/ansible/.ssh/authorized_keys
sudo pkill -KILL -u ansible
```

Rotate through the human login, restore mode `0600`, and rerun bootstrap.

## Storage and recovery

The ZFS role imports the existing pool without mounting legacy datasets and creates
only the isolated `zfspool/k8s` parent when enabled. It never destroys a pool or adopts
old PVC datasets. Preserve those datasets until recovery is proven independently.

The GPU package remains opt-in after checking `ubuntu-drivers list --gpgpu` and
matching Ubuntu support. Applications and device plugins remain Flux-owned.

See [cluster operations](../docs/rke2-operations.md) for bootstrap, maintenance,
firewall handoff and recovery. The existing OS controls are recorded in
[the CIS mapping](../docs/cis-control-mapping.md).
