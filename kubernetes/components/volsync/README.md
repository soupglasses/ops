<!--
SPDX-FileCopyrightText: 2026 Sofie <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# components/volsync

Per-app PVC backup + auto-restore-on-PVC-create against a restic S3 repo.

## Resources

| File | Purpose |
|---|---|
| `persistentvolumeclaim.yaml` | The PVC the app consumes. `dataSourceRef` points at the RD so populator restores it on creation. |
| `replicationsource.yaml` | RS: scheduled backup of the source PVC into restic. |
| `replicationdestination.yaml` | RD: restores from restic into an internal volume and exposes a `latestImage` snapshot for the populator. |
| `secret.yaml` | Restic credentials (repo URL + password + S3 creds) templated from `cluster-secrets`. |

## Consuming app contract

Set these in the app's `kustomize.yaml` `postBuild.substitute`:

| Var | Default | Notes |
|---|---|---|
| `APP` | required | PVC name = `${APP}`; RD name = `${APP}-dst`. |
| `VOLSYNC_CAPACITY` | `5Gi` | PVC size. |
| `VOLSYNC_CACHE_CAPACITY` | `5Gi` | Disposable restic cache size. |
| `VOLSYNC_CACHE_SNAPSHOTCLASS` | `openebs-zfs` | Cache StorageClass; ZFS enforces capacity independently of the root filesystem. |
| `VOLSYNC_STORAGECLASS` | `openebs-zfs` | Must support CSI snapshots (RS `copyMethod: Snapshot`). |
| `VOLSYNC_SNAPSHOTCLASS` | `openebs-zfs-snapshot` | Matching VolumeSnapshotClass. |
| `VOLSYNC_SCHEDULE` | `15 6 * * *` | Cron for the RS backup. |

## Run backups before maintenance

From the repository root, trigger every scheduled restic ReplicationSource and wait:

```bash
task volsync:replication-sync
```

The command shows each application's progress and a periodic completion count. It
waits for any existing run to finish before requesting a fresh backup, and returns
success only after every requested run reports a newer successful completion.
The default timeout is thirty minutes for the batch. To change it or select one
namespace:

```bash
task volsync:replication-sync TIMEOUT=1h
task volsync:replication-sync -- --namespace identity
```

Paused sources, non-restic sources, missing schedules and existing manual overrides
are reported as errors. On completion, failure, timeout or interruption, the helper
removes only its own manual trigger and preserves schedules. Interruption does not
cancel controller jobs; existing quiesce watchers remain responsible for resuming
applications. A nonzero exit means the batch is not a completed recovery checkpoint.

This uses VolSync's [manual-trigger API](https://volsync.readthedocs.io/en/stable/usage/triggers.html)
through `kubectl`. The native `kubectl volsync replication sync` manages saved rsync
relationships and reapplies their configuration; it does not target these Git-managed
restic sources. Application quiesce hooks, snapshots, uploads and retention remain
owned by the existing controllers.

This command confirms backup execution, not restorability. Before a destructive
recovery drill, verify application restores and independently preserved recovery keys,
then follow the [clean bootstrap guide](../../../docs/cluster-bootstrap.md).
Etcd snapshots are not part of recovery. The backup command does not reset RKE2.

## Restore before first start

The populator blocks PVC binding until the ReplicationDestination has a `latestImage`.
VolSync can initialize a missing repository and publish an **empty** image when no
snapshot matches. A Bound PVC or successful mover therefore does not prove recovery.
This component has no empty-seed Job. OpenLDAP and Minecraft additionally check for
their database/world files before starting; their backup hooks withhold the snapshot
trigger when the application is unavailable or backup preparation fails.

For existing applications, confirm a known-good off-cluster backup before reconciliation.
For a genuinely new application, explicitly initialize and validate its data, take the
first backup, then adopt this component. Never use that path to bypass missing history.
See [the recovery sequence](../../../docs/rke2-operations.md#application-recovery).

## Recovery from the backup repository

Normal operation only backs up and prunes through the ReplicationSource. There are
no scheduled repository checks or restore-image refreshes. Each application's
restic repository is its off-cluster recovery source; local snapshots are not a
replacement for that repository.

A clean cluster creates the one-shot ReplicationDestination and the PVC together.
The destination pulls the latest backup, and the populator blocks PVC binding until
that restore is ready. The destination has no recurring trigger.

For PVC-only recovery on an existing cluster, use `scripts/volsync-restore.sh`
through the tasks below. It requests a fresh restore from the same repository
before replacing the claim. Deleting only the PVC can reuse the destination's old
local image; it does not request the latest remote backup. For a complete storage
rebuild, recreate the destination as well as the PVC so the one-shot restore runs.

The helper suspends the application's Flux Kustomization, HelmRelease and backup
source. For an operator-owned VMSingle it also pauses the operator resource. It
stops the workload, requires every selected pod to terminate, creates a ready
**Retain** rollback snapshot, and replaces the claim with the same declared recovery
source. Application validation runs before these holds are released. Preexisting
holds remain in place. Failure or interruption after shutdown leaves writers stopped,
controllers held, and the rollback snapshot retained; inspect the failed operation
before manually releasing its `ops.soupglasses.dev/recovery-operation` annotations.

`VERIFY_COMMAND` is required. It runs locally with `APP`, `NS`, and `WORKLOAD` exported
and must fail unless known restored contents are usable through the application.
A readiness response or directory existing is insufficient. Use a reviewed check
for a known world, directory identity and group, metric series, or log record.
Do not put credentials in this argument or print them from the check.

```bash
task volsync:restore APP=minecraft VERIFY_COMMAND="$RECOVERY_CHECK"
task volsync:restore-pick APP=minecraft VERIFY_COMMAND="$RECOVERY_CHECK"
task volsync:restore-at APP=minecraft AT=<checkpoint-time> VERIFY_COMMAND="$RECOVERY_CHECK"
# OpenLDAP uses APP=openldap-directory NS=identity WORKLOAD=deployment/openldap.
```

`RECOVERY_CHECK` is an operator-supplied command, not a bundled acceptance test. For a data
migration rollback, pair the pre-upgrade data checkpoint with its original image
and configuration. Stop writers and keep controllers held while preparing that
state; rolling back only the image against migrated data is unsafe. The helper does
not automatically choose an image, undo schema changes, or prove downgrade support.
Keep backups paused until the restored application's contents and desired Git state
agree. The retained snapshot preserves the state immediately **before restore**;
it does not replace a known-good **pre-upgrade** off-cluster checkpoint.

After validation, patch the retained snapshot's bound `VolumeSnapshotContent`
deletion policy to `Delete` before deleting the `VolumeSnapshot`; otherwise its
backend ZFS snapshot remains. Do not discard either recovery point during diagnosis.

## Recovery checks in CI

The path-scoped Application recovery workflow runs the restore CLI's controller,
concurrency and failure cases, then starts real VictoriaMetrics and VictoriaLogs
with disposable storage and restic repositories. Each drill writes data through the
native API, backs up a stopped application, opens it with the candidate image,
writes more data, and verifies both recovery points through that API. The older
checkpoint is reopened with the base branch's image. It also reads all synthetic
repository data with `restic check --read-data` and verifies injected corruption fails.
These reads use disposable local test repositories, not production backups.

The OpenLDAP workflow runs its production backup hook on a synthetic directory,
imports the paired exports into new native databases, and checks password login,
group membership, directory access and rollback of a later attribute change.
These tests use no cluster credentials or production data. Run them locally with:

```bash
APPLICATION_RECOVERY_TEST=1 mise exec -- python3 -m unittest discover -s scripts -p test_application_data_recovery.py -v
OPENLDAP_ACCESS_TEST=1 mise exec -- python3 -m unittest scripts.test_openldap_access.NativeAccess.test_native_backup_restores_identity_password_and_group_membership -v
```

These checks do not prove CSI/populator races, S3 availability, independent cold
credentials, protected historical backups, or Minecraft world recovery. Live backup
success is not restore proof. Track those gaps in
[#121](https://github.com/soupglasses/ops/issues/121),
[#123](https://github.com/soupglasses/ops/issues/123),
[#127](https://github.com/soupglasses/ops/issues/127),
[#128](https://github.com/soupglasses/ops/issues/128) and
[#129](https://github.com/soupglasses/ops/issues/129).
Jellyfin configuration and media intentionally have no backups and are excluded.

For standalone restic checks, VolSync's `backup .` stores relative volume contents:
restore `config/`, `db/` and `backup/` into the chosen data directory, without an extra
`data/` archive prefix. Assert required files exist before native validation; a restore
whose include filters match nothing can still exit successfully.
