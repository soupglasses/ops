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

Set these in the app's `ks.yaml` `postBuild.substitute`:

| Var | Default | Notes |
|---|---|---|
| `APP` | required | PVC name = `${APP}`; RD name = `${APP}-dst`. |
| `VOLSYNC_CAPACITY` | `5Gi` | PVC size. |
| `VOLSYNC_CACHE_CAPACITY` | `5Gi` | Restic cache PVC size; metadata only, keep small. |
| `VOLSYNC_STORAGECLASS` | `openebs-zfs` | Must support CSI snapshots (RS `copyMethod: Snapshot`). |
| `VOLSYNC_SNAPSHOTCLASS` | `openebs-zfs-snapshot` | Matching VolumeSnapshotClass. |
| `VOLSYNC_SCHEDULE` | `15 6 * * *` | Cron for the RS backup. |

## Restore before first start

The populator blocks PVC binding until the ReplicationDestination has a `latestImage`.
That is intentional: a missing or inaccessible repository must not become an empty
application. This component does not initialize restic repositories or seed snapshots.

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
