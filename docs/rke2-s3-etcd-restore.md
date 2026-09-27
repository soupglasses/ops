<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# RKE2 encrypted etcd disaster recovery

This runbook restores the single-node `home` control plane onto a clean Ubuntu host
from a client-encrypted restic recovery point in S3. Use the RKE2 release recorded
for that recovery point. It restores Kubernetes state, not application volumes;
those come from VolSync after the control plane is stable. Native plaintext S3
uploads have been retired; do not enable them to perform recovery.

Use this procedure only when the selected etcd snapshot is known-good and the original RKE2 token is
available. If no trustworthy etcd snapshot exists, stop and use a clean GitOps bootstrap followed by
VolSync restores instead. Never mix the two control-plane recovery paths.

## Recovery objectives and retention

The host restic timer creates and restore-verifies a backup every six hours.
Successful uninterrupted runs provide an RPO of at most six hours. Failed or stale
verification invalidates that RPO. All encrypted recovery points are currently
retained without pruning, including imported native snapshot history.

Backups must survive loss of the node and ZFS pool. Preserve independent recovery
credentials and an off-cluster repository copy, and retain independent object history
that the normal writer cannot permanently erase. The existing SOPS recovery identity,
original RKE2 token and restic/S3 credentials are required. See the
[encrypted backup runbook](rke2-restic-backup.md) for tool bootstrap, artifact checksums,
repository selection, metadata layouts and verified recovery evidence.

## Stop conditions

Do not run `--cluster-reset` unless all of these are true:

- the host is disposable or the current database has an independently preserved rollback copy;
- the installed binary matches the selected recovery point's RKE2 release;
- `/etc/rancher/rke2/config.yaml` contains the original stable token and is mode `0600`;
- standalone restic has restored the chosen recovery point, its SHA-256 matches the
  encrypted metadata, and the matching native etcd utility validates the database;
- the selected snapshot predates the incident and is compatible with the intended VolSync recovery
  points;
- the off-cluster SOPS identities, RKE2 token, S3 credentials, and repository are independently
  available;
- physical console access works.
- no preserved application dataset can be attached by a restored OpenEBS workload; physically detach
  the old data disks or choose the clean GitOps path if that isolation cannot be guaranteed.

Stop on any checksum, decompression, S3, token, or etcd error. Do not remove `reset-flag`, delete
`etcd-old-*`, rerun reset on the same failed database, bootstrap Flux, or start application recovery.
Capture the output and rebuild the clean host before retrying with a corrected token or snapshot.

## 1. Prepare the clean node

Install Ubuntu 26.04 and OpenSSH as documented, restore repository access and the SOPS identity, and
set the exact controller `/32` and `/128` in the non-secret Ansible inventory. Do not manually mount
the old application datasets and do not bootstrap Flux. The ZFS role imports the existing pool with
`-N` so it can inventory it, but that is not strong enough isolation for restored OpenEBS objects.

First install the [standalone recovery tools](rke2-restic-backup.md#recovery-without-a-surviving-cluster).
Download and validate the selected backup before starting Kubernetes. This needs
only the independent S3/restic credentials, not a surviving API or Flux Secret.

From `ansible/`, bootstrap and converge the clean host with the original encrypted
inventory. Set the RKE2 version and matching artifact checksum to the selected backup's
release before convergence; the current inventory pin is not necessarily that release:

```bash
ansible-playbook playbooks/bootstrap.yml \
  -u sofie -e initial_ansible_host=172.21.71.254
ansible-playbook playbooks/network.yml \
  -u ansible -e initial_ansible_host=172.21.71.254
ANSIBLE_VARS_ENABLED=host_group_vars,community.sops.sops \
  ansible-playbook playbooks/site.yml
```

This initially creates a disposable empty control plane so Ansible can validate the pinned binary and
configuration. It must not contain a new Flux bootstrap. On the server, verify the version and secret
file permissions without printing their contents:

```bash
sudo /usr/local/bin/rke2 --version
sudo stat -c '%a %U:%G' /etc/rancher/rke2/config.yaml
sudo awk -F: '$1 == "token" { found=1 } END { exit !found }' \
  /etc/rancher/rke2/config.yaml
```

The `stat` result must be `600 root:root`. If any check fails, stop and fix Ansible inputs; never paste
the token into a shell argument or command history.

Before restoring etcd, isolate the preserved pool. Confirm every old dataset reports `mounted=no`,
stop RKE2, disable the non-mounting import unit, export the pool cleanly, shut down, and physically
detach the old data disks:

```bash
sudo zfs list -H -r -o name,mounted zfspool
sudo systemctl stop rke2-server
sudo systemctl disable zfspool-import-nomount.service
sudo zpool export zfspool
if sudo zpool list zfspool >/dev/null 2>&1; then exit 1; fi
sudo systemctl poweroff
```

Do not export if any dataset is mounted or any process is using the pool. Preserve the detached disks
unchanged as the local rollback point. Boot without them and confirm `zpool list zfspool` fails before
continuing. If physical isolation is impossible, stop and use the clean GitOps-plus-VolSync path.

## 2. Select and restore the encrypted snapshot

On the trusted recovery host, supply restic/S3 credentials through protected
credential handling, with shell tracing disabled. As root:

```bash
umask 077
restic snapshots --tag rke2-etcd
restic snapshots --tag rke2-etcd-history
restic check --read-data
restic restore SELECTED_RESTIC_SNAPSHOT_ID --target /protected/recovery
```

Record the exact restic ID, original snapshot time, incident time and selection
reason. Imported history has a new import timestamp: use `recovery.json` to identify
the original recovery time, not the date of the restic import. Select an older point
if corruption could predate the latest backup.

New backups record their RKE2 release in `recovery.json`. Imported history preserves
original S3 metadata, which may omit the RKE2 release. In that case, correlate the
original snapshot timestamp with the recorded upgrade timeline: this cluster used
`v1.34.11+rke2r1` before the 2026-09-26 upgrade to `v1.35.8+rke2r1`.
Establish the exact upgrade boundary from retained change records for snapshots
near that date. Stop if a compatible RKE2 release cannot be established; neither the
import timestamp nor the current inventory pin proves the original version.

New backups contain `work/source/snapshot.db`. Imported history contains
`work/source/original` and its `recovery.json`; select a database object, not a metadata
companion or coverage index. Validate the original SHA-256 against encrypted metadata.
For a compressed historical object, extract its single validated ZIP member to a
protected database file before the native check. The historical migration preserved
original bytes and object metadata. See the backup runbook for the complete layouts.

Set `SNAPSHOT_PATH` to the absolute local database path, and validate it:

```bash
SNAPSHOT_PATH=/protected/recovery/work/source/snapshot.db
/opt/etcd-recovery/etcd-v3.6.14-linux-amd64/etcdutl snapshot status \
  "$SNAPSHOT_PATH" --write-out=json
```

Use the validator version recorded for the selected snapshot. Do not continue on a
checksum, archive, version or native validation failure. The local file contains
sensitive cluster state; retain root-only permissions throughout recovery.

## 3. Restore and reset the single-member cluster

Stop RKE2 and confirm it is inactive:

```bash
sudo systemctl stop rke2-server
if sudo systemctl is-active --quiet rke2-server; then exit 1; fi
```

Run the restore in the foreground. RKE2 reads the original token from its root-only
configuration. Explicitly disable native S3 access: restic already downloaded and
decrypted the selected database, and the restore path is an absolute local path:

```bash
sudo /usr/local/bin/rke2 server \
  --etcd-s3=false \
  --cluster-reset \
  --cluster-reset-restore-path="$SNAPSHOT_PATH"
```

Proceed only after the command reports:

```text
Managed etcd cluster membership has been reset, restart without --cluster-reset flag now.
```

RKE2 verifies the local snapshot, moves any current database to an `etcd-old-*` directory,
extracts the datastore, reduces membership to this server, and restores the snapshot CAs. Do not
delete the old directory or `/var/lib/rancher/rke2/server/db/reset-flag`; a normal successful start
removes the reset flag.

Start normally and wait for the API:

```bash
sudo systemctl start rke2-server
sudo /var/lib/rancher/rke2/bin/kubectl \
  --kubeconfig /etc/rancher/rke2/rke2.yaml get --raw=/readyz
```

If normal startup fails, stop RKE2 and preserve the entire data directory and journal. On this clean
single-node replacement, rollback means reprovisioning the node and repeating from a known-good encrypted
recovery point; it does not mean deleting RKE2's safety files or overwriting the preserved database in place.

## 4. Freeze reconciliation before storage recovery

An etcd snapshot already contains Flux objects. Do **not** run `flux bootstrap`; doing so creates two
competing recovery timelines. As soon as the API is ready, pause restored Flux controllers:

```bash
KUBECTL='sudo /var/lib/rancher/rke2/bin/kubectl --kubeconfig /etc/rancher/rke2/rke2.yaml'
$KUBECTL -n flux-system scale deployment --all --replicas=0
$KUBECTL get statefulset,deployment -A
```

Scale every application workload that can write persistent data to zero before touching its PVC.
Leave RKE2, Cilium, CoreDNS, OpenEBS, and VolSync infrastructure running. A restored PVC or Ready pod
does not prove that its old ZFS volume exists or that its data is usable.

If the restored etcd state has obsolete node objects, remove them only after the replacement `nas`
node is Ready. Do not delete PVCs or PVs generically; follow each workload's reviewed recovery steps.

## 5. Restore applications, then resume Flux

The recovery order is strict:

1. Validate RKE2, etcd, Cilium dual stack, and API access.
2. Provision approved replacement storage and the empty `zfspool/k8s` target while the detached old
   pool remains untouched. If no independent replacement storage exists, stop; do not destroy the
   rollback disks merely to make space for the restore.
3. Validate OpenEBS and VolSync controllers without starting application writers.
4. Select an application recovery point at or before the desired consistency boundary.
5. Restore one application through its `ReplicationDestination` and PVC `dataSourceRef` contract.
6. Validate application-level contents, not just PVC binding or pod readiness.
7. Preserve the old ZFS datasets and S3 recovery points until every workload passes its restore drill.
8. Repeat for the next application.
9. Scale Flux controllers back to their repository-defined replica count and reconcile only after the
   live state and Git state agree.

Use `docs/openldap-volsync-migration.md` as the restore-drill pattern. If a required storage resource
is absent, reconcile only the storage/VolSync prerequisites first; do not enable the entire
`cluster-apps` graph and let all writers race their restores.

## 6. Post-restore validation

Run the host checks and record their output in the recovery issue:

```bash
cd ansible
ansible-playbook playbooks/verify.yml
ssh router '/routing/bgp/session/print detail'
```

After restoring Flux host-policy ownership, run `playbooks/verify-host-firewall.yml`.

Also require:

- the API and SSH work through new IPv4 and IPv6 connections;
- both authenticated Cilium BGP sessions are established and only the `/32` and `/128` VIPs appear;
- `systemctl start rke2-etcd-restic-backup.service` succeeds after Flux restores its
  credential reference, with a new verified restore receipt, enabled timer and
  ingested success/freshness metrics;
- every restored application passes a content or login test;
- VolSync restores are proven from off-cluster data;
- Flux resumes without pruning a recovery resource or recreating a stale volume;
- the old ZFS datasets and selected S3 versions remain untouched until final resilience approval.

Only after this evidence exists may the recovery be declared complete or any old local rollback point
be removed.

## Upstream references

- [RKE2 backup and restore](https://docs.rke2.io/datastore/backup_restore)
- [RKE2 server configuration](https://docs.rke2.io/reference/server_config)
- [RKE2 v1.34 release notes](https://docs.rke2.io/release-notes/v1.34.X)
