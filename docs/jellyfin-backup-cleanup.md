<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: MIT
-->

# Jellyfin backup cleanup

Deploy the no-backup manifests before cleanup. Removed resources persist because
the old Kustomizations use `prune: false`. Preserve the live `jellyfin` and
`jellyfin-media` PVCs, their PVs and the NAS dataset.

Run these commands yourself with an authenticated Kubernetes context. These commands
irreversibly remove Jellyfin backup history. Do not enable shell tracing.

## Stop backup resources

```bash
mise exec -- kubectl -n media rollout status deployment/jellyfin --timeout=5m
mise exec -- kubectl -n media get deployment jellyfin -o yaml
```

Confirm the deployment has no restore init containers or checkpoint mount before
continuing. Never delete the live configuration or media PVC.

```bash
mise exec -- kubectl -n media delete kustomization jellyfin-media-backup --ignore-not-found
mise exec -- kubectl -n media delete deployment jellyfin-volsync-quiesce --ignore-not-found
mise exec -- kubectl -n media delete replicationsource jellyfin jellyfin-media --ignore-not-found
mise exec -- kubectl -n media delete replicationdestination jellyfin-dst --ignore-not-found
mise exec -- kubectl -n media delete job jellyfin-media-restore-v2 --ignore-not-found
mise exec -- kubectl -n media delete rolebinding jellyfin-volsync-quiesce --ignore-not-found
mise exec -- kubectl -n media delete role jellyfin-volsync-quiesce --ignore-not-found
mise exec -- kubectl -n media delete serviceaccount jellyfin-volsync-quiesce --ignore-not-found
mise exec -- kubectl -n media get jobs,pods,pvc,volumesnapshots -o wide
```

Wait until dependent mover Jobs and Pods are gone. Existing bound configuration PVCs
may retain their immutable `dataSourceRef`; the new manifest uses `IfNotPresent` to
preserve them. A new installation creates an ordinary PVC without restore dependencies.

## Delete the two restic histories

Run this Bash block yourself. It reads credentials from the two existing Secrets into
subprocess environments without displaying or saving them. Repository suffix checks
restrict deletion to Jellyfin. Keep the Secrets until this block succeeds.

```bash
set -euo pipefail
set +x
mise exec -- restic version
for repo in volsync-jellyfin volsync-jellyfin-media; do
  (
    unset RESTIC_REPOSITORY_FILE RESTIC_PASSWORD_FILE RESTIC_PASSWORD_COMMAND RESTIC_HOST RESTIC_TAG
    for key in RESTIC_REPOSITORY RESTIC_PASSWORD AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY; do
      value=$(mise exec -- kubectl -n media get secret "$repo" \
        -o "jsonpath={.data.$key}" | base64 --decode)
      test -n "$value"
      export "$key=$value"
    done
    unset value
    case "$RESTIC_REPOSITORY" in
      s3:*/"$repo") ;;
      *) printf 'Refusing unexpected repository for %s\n' "$repo" >&2; exit 1 ;;
    esac
    snapshot_json=$(mise exec -- restic snapshots --no-cache --json)
    snapshot_ids=$(printf '%s' "$snapshot_json" | mise exec -- jq -er \
      'if type == "array" then map(.id) | join("\n") else error("invalid snapshots") end')
    if [[ -n "$snapshot_ids" ]]; then
      mapfile -t ids <<< "$snapshot_ids"
      mise exec -- restic forget --no-cache --prune "${ids[@]}"
    else
      mise exec -- restic prune --no-cache
    fi
    mise exec -- restic snapshots --no-cache --json | mise exec -- jq -e 'length == 0'
    mise exec -- restic check --no-cache
  )
done
```

This removes all snapshots and unreferenced data in those repositories. Empty
repository keys and configuration remain. S3 versioning, retention or object locks
may retain historical bytes beyond restic's control; clean these through the provider,
restricted to the same two prefixes. Never delete the shared bucket. If restic reports
a lock, identify and stop its holder before proceeding; do not blindly unlock.

## Remove local leftovers

After the deployment rollout and controller deletion complete:

```bash
mise exec -- kubectl -n media delete pvc jellyfin-checkpoint --ignore-not-found
mise exec -- kubectl -n media delete secret volsync-jellyfin volsync-jellyfin-media --ignore-not-found
mise exec -- kubectl -n media get pvc,volumesnapshots,jobs,pods -o wide
```

VolSync normally garbage-collects resources owned by removed controllers. Inspect
remaining cache PVCs and VolumeSnapshots with `kubectl -n media get KIND NAME -o yaml`.
Delete individual names only after tracing owner references, labels and source PVCs
to `jellyfin`, `jellyfin-dst` or `jellyfin-media`, including snapshots sourced from
`jellyfin-checkpoint`. Preserve every unrelated resource. Never delete cluster-scoped
VolumeSnapshotContents using broad name filters.
