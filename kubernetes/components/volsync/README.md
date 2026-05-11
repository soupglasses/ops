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
| `bootstrap.yaml` | One-shot Job that seeds an empty snapshot into restic on first deploy, then re-triggers the RD. Idempotent; no-ops once real backups exist. |

## Consuming app contract

Set these in the app's `ks.yaml` `postBuild.substitute`:

| Var | Default | Notes |
|---|---|---|
| `APP` | required | PVC name = `${APP}`; RD name = `${APP}-dst`. |
| `VOLSYNC_CAPACITY` | `5Gi` | PVC size. |
| `VOLSYNC_STORAGECLASS` | `openebs-zfs` | Must support CSI snapshots (RS `copyMethod: Snapshot`). |
| `VOLSYNC_SNAPSHOTCLASS` | `openebs-zfs-snapshot` | Matching VolumeSnapshotClass. |
| `VOLSYNC_SCHEDULE` | `15 6 * * *` | Cron for the RS backup. |

## Bootstrap behaviour

Volsync's populator deliberately blocks PVC binding when the RD has no
`latestImage`. There is no upstream fallback-to-empty option ([discussion](https://github.com/backube/volsync/discussions/1414)).
`bootstrap.yaml` works around this: on first deploy it seeds the restic repo
with one empty snapshot, then patches the RD's `trigger.manual` to make the
mover restore the seed and produce a `latestImage`. The PVC then binds empty,
the app starts, and real RS backups take over from there.

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
waits for `latestImage` to refresh, scales the workload to 0, deletes + rebinds
the PVC, then scales the workload back up. Point-in-time variants:

```bash
task volsync:restore APP=minecraft                          # newest snapshot
task volsync:restore-pick APP=minecraft                     # interactive picker
task volsync:restore-at APP=minecraft AT=2026-05-01T12:00:00Z
```

Switching to `trigger.schedule` would refresh automatically at the cost of an
extra restore cycle per period; we accept the trade because cluster-rebuild
is the actual disaster scenario and PVC-only recovery is rare and scripted.
