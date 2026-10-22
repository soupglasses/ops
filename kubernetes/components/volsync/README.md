<!--
SPDX-FileCopyrightText: 2026 Sofie <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# components/volsync

Per-app PVC backup + auto-restore-on-PVC-create against a restic S3 repo.

## Resources

| File | Purpose |
|---|---|
| `pvc.yaml` | The PVC the app consumes. `dataSourceRef` points at the RD so populator restores it on creation. |
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
VolSync 0.15 can initialize a missing repository and publish an **empty** image when no
snapshot matches. A Bound PVC or successful mover therefore does not prove recovery.
This component has no empty-seed Job. OpenLDAP and Minecraft additionally check for
their database/world files before starting; their backup hooks withhold the snapshot
trigger when the application is unavailable or backup preparation fails.

For existing applications, confirm a known-good off-cluster backup before reconciliation.
For a genuinely new application, explicitly initialize and validate its data, take the
first backup, then adopt this component. Never use that path to bypass missing history.
See [the recovery sequence](../../../docs/rke2-operations.md#application-recovery).

## Gotcha: RD `latestImage` is frozen between full rebuilds

The RD uses `trigger.manual: restore-once`, so it fires **once** at creation
and then never again. Implications:

- **Full cluster rebuild** (RD recreated from scratch): RD fires on creation,
  pulls the newest restic snapshot, populator restores newest. ✅
- **PVC-only deletion** (RD intact, only the PVC re-created): populator copies
  the *frozen* `latestImage`, which is whatever the RD restored at its first
  fire. New RS backups don't update it. ⚠️

To force a fresh restore without a full rebuild, use `scripts/volsync-restore.sh`
(also exposed via `task volsync:restore APP=<name>`). It bumps the RD trigger,
waits for `latestImage` to refresh, scales the workload to 0, preserves the
current PVC in a `VolumeSnapshot`, deletes + rebinds the PVC, then scales the
workload back up. The rollback snapshot uses the Retain class. After application
validation, patch its bound `VolumeSnapshotContent` deletion policy to `Delete`
before deleting the VolumeSnapshot so the controller also cleans up the backend
ZFS snapshot. Point-in-time variants:

```bash
task volsync:restore APP=minecraft                          # newest snapshot
task volsync:restore-pick APP=minecraft                     # interactive picker
task volsync:restore-at APP=minecraft AT=2026-05-01T12:00:00Z
```

Switching to `trigger.schedule` would refresh automatically at the cost of an
extra restore cycle per period; we accept the trade because cluster-rebuild
is the actual disaster scenario and PVC-only recovery is rare and scripted.

## Manual backup validation

On 2026-09-27, the bulk task completed fresh backups for Minecraft, OpenLDAP,
VictoriaLogs and VictoriaMetrics in about 36 seconds. All four reported successful
new completion times, their original schedules remained, and no temporary manual
triggers remained. A scoped monitoring run also verified mover waiting/running
progress. Fourteen local tests cover failures, stale status, concurrency and cleanup.

The fresh OpenLDAP snapshot `e9a1c0ff` was restored into an isolated memory-backed
Pod. Its native database export exactly matched the paired backup export: 89 entries,
including four `inetOrgPerson` entries, with SHA-256
`3a6f4e5ffae23f300c76094ce6de638629a705459392988837282aa17fd61f1a`.
The Pod and plaintext were removed afterward. This verifies that recovery point;
other applications still need application-level restore checks before a destructive drill.

For standalone restic checks, VolSync's `backup .` stores relative volume contents:
restore `config/`, `db/` and `backup/` into the chosen data directory, without an extra
`data/` archive prefix. Assert required files exist before native validation; a restore
whose include filters match nothing can still exit successfully.
