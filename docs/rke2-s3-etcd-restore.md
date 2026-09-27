<!--
SPDX-FileCopyrightText: 2026 Soup Bot <bot+ops@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# RKE2 S3 etcd disaster recovery

This runbook restores the single-node `home` control plane onto a clean Ubuntu host from an RKE2
snapshot in S3. It is version-matched to `v1.34.11+rke2r1`. It restores Kubernetes state, not the
contents of application volumes; those come from VolSync only after the control plane is stable.

Use this procedure only when the selected etcd snapshot is known-good and the original RKE2 token is
available. If no trustworthy etcd snapshot exists, stop and use a clean GitOps bootstrap followed by
VolSync restores instead. Never mix the two control-plane recovery paths.

## Recovery objectives and retention

RKE2 takes compressed snapshots every six hours and retains 28 locally and 28 in S3. Successful,
uninterrupted runs therefore provide an RPO of at most six hours and seven days of directly selectable
history. Failed or stale uploads invalidate that RPO.

The S3 bucket must:

- have versioning, object lock, or an equivalent independent history mechanism enabled;
- retain noncurrent or deleted objects for at least 30 days, longer than RKE2's rolling seven days;
- survive loss of the node and ZFS pool;
- prevent the RKE2 writer credential from permanently erasing the only copy;
- have enough lifecycle headroom that its policy never expires a current object before RKE2's
  `etcd-s3-retention: 28` policy does.

RKE2 deletion of an older current object may create a noncurrent version. Recovering one of those
older versions requires an S3 administrator to copy it back under a new current object name before
running this procedure. Record that provider-specific operation outside the cluster.

## Stop conditions

Do not run `--cluster-reset` unless all of these are true:

- the host is disposable or the current database has an independently preserved rollback copy;
- the installed binary reports exactly `v1.34.11+rke2r1`;
- `/etc/rancher/rke2/config.yaml` contains the original stable token and mode-`0600` S3 settings;
- `rke2 etcd-snapshot ls` lists the selected object by filename;
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

From `ansible/`, bootstrap and converge the clean host with the original encrypted inventory:

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
sudo /usr/local/bin/rke2 --version | grep -F 'v1.34.11+rke2r1'
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

## 2. Select the S3 snapshot

List S3 snapshots using the credentials already held in the mode-`0600` RKE2 config:

```bash
sudo /usr/local/bin/rke2 etcd-snapshot ls
```

Record the exact filename, creation time, incident time, and reason it is considered known-good in the
recovery issue. Select an older point if corruption may predate the latest backup. Set only the base
filename, never an S3 URL or local path:

```bash
SNAPSHOT_NAME='REPLACE_WITH_EXACT_S3_SNAPSHOT_FILENAME'
test -n "$SNAPSHOT_NAME"
```

## 3. Restore and reset the single-member cluster

Stop RKE2 and confirm it is inactive:

```bash
sudo systemctl stop rke2-server
if sudo systemctl is-active --quiet rke2-server; then exit 1; fi
```

Run the restore in the foreground. RKE2 reads the original token and all S3 settings from
`/etc/rancher/rke2/config.yaml`; because S3 is enabled, the restore path is the filename only:

```bash
sudo /usr/local/bin/rke2 server \
  --cluster-reset \
  --cluster-reset-restore-path="$SNAPSHOT_NAME"
```

Proceed only after the command reports:

```text
Managed etcd cluster membership has been reset, restart without --cluster-reset flag now.
```

RKE2 downloads and verifies the snapshot, moves any current database to an `etcd-old-*` directory,
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
single-node replacement, rollback means reprovisioning the node and repeating from a known-good S3
object; it does not mean deleting RKE2's safety files or overwriting the preserved database in place.

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
- `rke2 etcd-snapshot ls` succeeds and a new post-recovery snapshot uploads;
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
