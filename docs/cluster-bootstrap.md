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

Create fresh application backups and then a separate encrypted etcd backup:

```bash
task volsync:replication-sync
task ansible:backup-etcd
```

The VolSync task shows per-app progress, waits for all new backups, and returns
nonzero if any source fails or times out. `TIMEOUT=1h` extends the default batch
limit; normal backup schedules and application preparation hooks remain in place.
The etcd task also restores and validates its encrypted backup before succeeding.
Neither command deletes data or resets the cluster.

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
- the original RKE2 token in encrypted inventory when restoring etcd.

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

## 2. Choose the recovery path

**For a clean rebuild or lost application storage, use the default bootstrap.** It
creates a new Kubernetes control plane from Git. New PVCs reference VolSync restore
destinations, so application data comes from backups before applications start.
Preserved old ZFS datasets are not adopted or deleted.

**To recover Kubernetes state with its existing application storage, select an encrypted
etcd snapshot.** Before normal RKE2 startup, the restore helper downloads that snapshot
using standalone restic, verifies its checksum and native etcd contents, checks version
compatibility, and verifies the recorded application dataset identities against ZFS.
Missing or mismatched datasets stop the restore. Snapshots without the required volume
inventory are not accepted by this automatic path.

An etcd snapshot contains Kubernetes objects and Secrets, **not application volume
contents**. Restoring an old Bound PVC does not make VolSync populate it again. Use
the clean rebuild path for storage loss; do not bypass a storage-identity failure.
For corruption recovery, select an older known-good etcd snapshot and assess application
recovery points separately. Dataset identity does not prove application contents are
uncorrupted.

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

To restore etcd as part of the same process, add one of these selectors before `--`:

```bash
task rke2:bootstrap RESTORE=latest
task rke2:bootstrap RESTORE=RESTIC_SNAPSHOT_ID
```

`latest` selects a normal encrypted etcd backup for this node, then resolves its exact
ID. It does not select the imported historical archives. Prefer a known-good snapshot
ID when corruption may predate the latest backup.

The task runs `ansible/playbooks/bootstrap-rke2-controlplane.yml`: accounts, networking,
host hardening, storage, configured GPU support, RKE2, optional encrypted restore, then
Flux installation and credential import. The restore happens **before the first normal
RKE2 start**. Native plaintext S3 uploads remain disabled. After a successful etcd reset,
the temporary plaintext snapshot is deleted; the protected recovery receipt remains.

After Flux reconciliation, bootstrap runs the same cleanup as `task kube:clean-pods`
through the host connection. It deletes only Failed and Succeeded pods; Pending pods
remain available for ongoing storage restores. The resumed Flux handoff also runs this
cleanup. For an interactive node debugging shell, use `task kube:shell NODE=nas`.

Bootstrap refuses an existing etcd database. It never deletes one or falls back to an
empty cluster when a requested restore fails. Use `task ansible:apply` for routine host
convergence. If host setup completed but the Flux handoff failed, fix the reported cause
and resume only that phase:

```bash
task ansible:bootstrap-flux
```

Do not remove etcd directories or reset markers to get past a failed restore. Preserve
the failed host's state for diagnosis and retry recovery on a clean replacement.

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

Request a fresh encrypted backup and restore verification from the workstation:

```bash
task ansible:backup-etcd
```

On the server, inspect the encrypted backup timer and service without printing Secrets:

```bash
sudo systemctl list-timers rke2-etcd-restic-backup.timer
sudo systemctl status rke2-etcd-restic-backup.service
sudo cat /var/lib/rke2-restic-backup/status.json
```

Check Flux's backup alerts and the verified-backup timestamp. The scheduled job must
complete a fresh backup and restore verification after recovery. See
[encrypted etcd backups](rke2-restic-backup.md) for repository layout, restore evidence
and retention. Keep recovery keys and off-cluster backups independent of this cluster.

## References

- [RKE2 snapshot restore and original-token requirements](https://docs.rke2.io/datastore/backup_restore)
- [Restic recovery](https://restic.readthedocs.io/en/stable/050_restore.html)
- [VolSync PVC population](https://volsync.readthedocs.io/en/latest/usage/volume-populator/index.html)
