<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# Bootstrap and recover the home cluster

Run the bootstrap from a trusted workstation with this repository and independent
recovery credentials. Ansible configures the host and starts RKE2; Flux then rebuilds
applications from Git. VolSync restores application volumes from encrypted backups.
Container images and charts are fetched from their configured registries; VolSync
restores volume data, not container images. Registry access or a tested artifact mirror
is therefore also required. The inventory and tool configuration define supported versions; this guide deliberately
leaves version selection there.

## Before a planned recovery drill

Create fresh application backups:

```bash
task volsync:replication-sync
```

The VolSync task shows per-app progress, waits for all new backups, and returns
nonzero if any source fails or times out. `TIMEOUT=1h` extends the default batch
limit; normal backup schedules and application preparation hooks remain in place.
This command does not delete data or reset the cluster.

A completed upload alone is insufficient preparation for destruction. Validate
application restores, preserve the recovery keys independently, and keep rollback
data until the replacement cluster passes the checks below. See
[manual VolSync backups](../kubernetes/components/volsync/README.md#run-backups-before-maintenance).

## 1. Prepare the replacement host and workstation

Install a supported Ubuntu release, enable SSH and confirm the administrator can use
sudo. Have console access during the network handoff. Provision or attach the ZFS pool
named in the inventory: Ansible imports an existing pool but never formats disks or
creates a pool. Preserve old data disks and backups until application recovery is proven.

On the workstation, obtain:

- this repository, including its encrypted inventory and Kubernetes Secrets;
- the independent SOPS recovery identity and the administrator/automation SSH keys;
- access to the encrypted S3 backup repositories;
- the encrypted host inventory, including the configured RKE2 join token.

The repository contains encrypted credentials, not the key needed to decrypt them.
Do not rely on a surviving Kubernetes Secret, the failed node, or an agent workspace
as the only copy of a recovery key. Supply SOPS credentials through your usual protected
operator environment; never put secret values in command arguments or shell history.

Install the repository tools and Ansible dependencies:

```bash
mise install
python3 -m venv ansible/.venv
ansible/.venv/bin/pip install -r ansible/requirements.txt
export PATH="$PWD/ansible/.venv/bin:$PATH"
task ansible:deps
```

Review these files before running the bootstrap:

| File | What to check |
| --- | --- |
| `ansible/inventories/home/hosts.yml` | Host, SSH account/key and host-key alias |
| `ansible/inventories/home/group_vars/all/main.yml` | Network interfaces, final addresses, administrator source addresses, ZFS pool and release pins |
| `ansible/inventories/home/group_vars/all/secrets.sops.yaml` | Existing recovery token and router authentication credentials |

Verify the replacement host's SSH fingerprint through the console before updating
`known_hosts` for the inventory's host-key alias. Keep host-key checking enabled.

## 2. Understand the recovery boundary

Bootstrap always creates a fresh Kubernetes control plane from Git. New application
PVCs reference VolSync restore destinations and download their S3 recovery points.
Startup checks reject missing directory, world or database contents. Preserved old ZFS
datasets are not adopted or deleted; allow capacity for the restored copies.

Etcd backups and restoration are retired. There is no restore mode, old PVC binding
reuse, or fallback that initializes empty application data. Kubernetes credentials
come from encrypted Git sources and independent recovery keys. Controllers recreate
runtime state; applications recover their own data from backups.

For corruption recovery, select the application's older known-good point before
letting it start, using the [application recovery procedure](rke2-operations.md#application-recovery).
A successful download or Bound PVC alone is not acceptance: validate application
contents after restoration.

The current inventory and ZFS storage target one node. Multiple nodes require explicit
storage placement and backup access; rebuilding the control plane does not make
node-local ZFS replicated storage.

## 3. Run the bootstrap

From the repository root, using the inventory's existing SSH connection:

```bash
task rke2:bootstrap
```

For a newly installed host, pass its temporary address and administrator login. Replace
these example values with the installer address and a usable SSH key:

```bash
task rke2:bootstrap -- \
  -e initial_ansible_host=INSTALLER_ADDRESS \
  -e ansible_user=sofie \
  -e ansible_ssh_private_key_file="$HOME/.ssh/gpg.pub"
```

Use `initial_ansible_host`, not `ansible_host`: the network role reconnects at the final
inventory address. The administrator connection remains in use throughout this run.
An alternative inventory can be passed after `--` with `-i /path/to/inventory.yml`.

The task runs `ansible/playbooks/bootstrap-rke2-controlplane.yml`: accounts, networking,
host hardening, storage, configured GPU support, a fresh RKE2 control plane, then Flux
installation and credential import. Native etcd snapshots and S3 uploads stay disabled.
There are no `RESTORE`, `SNAPSHOT` or `MODE` options.

After Flux reconciliation, bootstrap runs the same cleanup as `task kube:clean-pods`
through the host connection. It deletes only Failed and Succeeded pods; Pending pods
remain available for ongoing storage restores. The resumed Flux handoff also runs this
cleanup. For an interactive node debugging shell, use `task kube:shell NODE=nas`.

Bootstrap refuses existing RKE2 server state, including a stopped or partially
initialized server. It does not wipe or adopt that state. Use `task ansible:apply` for routine host
convergence. If host setup completed but the Flux handoff failed, fix the reported cause
and resume only that phase:

```bash
task ansible:bootstrap-flux
```

If host setup stopped partway through, inspect the failure and use normal convergence
to finish that host, or start again on a clean replacement. Do not delete state simply
to bypass the bootstrap preflight.

## 4. Watch the cluster rebuild

Fetch and verify a fresh administrator kubeconfig after a rebuild:

```bash
task kube:kubeconfig
```

Use the kubeconfig location configured by the repository environment, or pass your own
output path to that task and export `KUBECONFIG` to match. Then watch reconciliation in
separate terminals:

```bash
flux get kustomizations -A --watch
flux get helmreleases -A --watch
kubectl get pods -A --watch
kubectl get pvc -A
```

Inspect the restore controllers and recent events when a volume is waiting:

```bash
kubectl get replicationdestinations,replicationsources -A
kubectl get volumesnapshots -A
kubectl get events -A --sort-by=.lastTimestamp
```

Pending PVCs can be normal while VolSync downloads data and publishes the restoration
snapshot. The PVC's `dataSourceRef` connects it to that destination. Do not remove the
reference, initialize a replacement backup repository, or bypass an application's
restored-data guard to make a Pod become Ready. A missing backup must remain a visible
failure rather than create an empty application.

If Git is reachable but reconciliation needs prompting:

```bash
task flux:reconcile
task ansible:verify
```

A Ready Pod or a successful mover is not enough to prove recovery. Check existing LDAP
users and group membership over TLS, the existing Minecraft world, and historical
VictoriaMetrics/VictoriaLogs queries. Confirm new writes work and a subsequent backup
can be restored before retiring rollback data. Application-specific procedures are in
[the VolSync contract](../kubernetes/components/volsync/README.md) and
[the LDAP restore drill](openldap-volsync-migration.md).

## 5. Renew access and verify backups

Once Flux has recreated Hermes ServiceAccounts, replace their credentials in the
existing Hermes container:

```bash
task hermes:deploy-keys HOST=satellite
```

For one profile and an explicit requested lifetime:

```bash
task hermes:deploy-keys HOST=satellite -- --profile observability --duration-seconds 604800
```

This issues and validates replacement credentials; it does not revoke still-valid old
tokens. Use the [Hermes access guide](hermes-cluster-access.md) for compromise response,
container prerequisites and first-time configuration. Record the actual expiry printed
by the command and renew before then.

After validating recovered application contents, request fresh backups:

```bash
task volsync:replication-sync
kubectl get replicationsources -A
```

Check application backup alerts and completion timestamps. Keep recovery keys and
S3 application backups independent of the cluster. See the
[recorded application restore evidence](rke2-recovery-verification.md) and
[LDAP restore validation](../kubernetes/components/volsync/README.md#manual-backup-validation).
A complete replacement-host Ansible run remains a separate drill; individual restore
checks do not establish that the whole rebuild is proven.

## References

- [Restic recovery](https://restic.readthedocs.io/en/stable/050_restore.html)
- [VolSync PVC population](https://volsync.readthedocs.io/en/latest/usage/volume-populator/index.html)

## Uninstall a node before a planned rebuild

After checking recovery keys, available storage capacity and application restore
results, use the inventory node name:

```bash
task rke2:uninstall NODE=nas
```

Ansible uses the inventory's SSH connection and sudo. The task first holds Flux's
writers, lets existing backups finish, requests fresh application backups and waits
for successful uploads and mover cleanup. It retains the acknowledged manual triggers
so scheduled backups cannot race teardown. A preparation failure aborts uninstall
and attempts to restore normal schedules and reconciliation.

Only then does it run RKE2's supplied `/usr/local/bin/rke2-uninstall.sh` on the selected
node. [RKE2 calls this operation uninstall](https://docs.rke2.io/install/uninstall).
The upstream script has no last-control-plane guard or force flag: it can remove the
last server. The task adds backup ordering, not a replacement node-removal algorithm.
It does not destroy the ZFS pool or the S3 application repositories.

The backup hold covers the cluster. With multiple inventory servers, preparation
prefers another server as its coordinator. On a surviving cluster, keep backups held
until affected applications have healthy restored storage, then explicitly resume on
that coordinator:

```bash
task volsync:resume NODE=nas
```

If uninstall fails after preparation, the hold remains because RKE2 may already be
partially removed. Inspect the failure before resuming. A clean bootstrap reconstructs
schedules and Flux from Git and removes the previous cluster's local hold journal;
it never applies the old journal to a new cluster. Writes after the completed backup
are outside that recovery point.

The hold and resume sequence was exercised on the running cluster on 2026-09-27
without invoking uninstall. All four application backups completed, their manual
triggers blocked further runs during the hold, and schedules plus Flux resumed.
LDAP recovery point `429e7903` then passed an isolated native restore: its export
matched the paired backup export (89 entries). The test Pod and plaintext were
removed. This verifies backup preparation, not a full replacement-host rebuild.
