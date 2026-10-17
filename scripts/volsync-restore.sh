#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Sofie <sofie+git@mailbox.org>
#
# SPDX-License-Identifier: MIT
#
# Force a volsync PVC to restore from restic.
#
# A destination latestImage may be stale until its next scheduled or manual
# refresh. Recreating the PVC alone would restore that stale image. This script
# bumps the RD trigger, waits for the mover to refresh latestImage, scales the
# workload to 0, deletes the PVC, kicks
# flux to re-create it, waits for the populator to bind it, then scales the
# workload back to its original replica count.
#
# Modes:
#   scripts/volsync-restore.sh <APP>                  # newest snapshot
#   scripts/volsync-restore.sh <APP> --pick           # interactive picker
#   scripts/volsync-restore.sh <APP> <RFC3339_TS>     # explicit PIT
#
# Env:
#   NS              namespace (default: default)
#   WORKLOAD        type/name to scale (default: auto — deployment/${APP}
#                   or statefulset/${APP})
#   FLUX_KS         Kustomization name to reconcile after PVC delete
#                   (default: ${APP} in ${NS}). Set empty to skip.
#   TIMEOUT         kubectl wait timeout (default: 5m)
#   RESTIC_IMAGE    image with restic+jq for the picker
#                   (default: quay.io/backube/volsync:0.15.0)
#   ROLLBACK_SNAPSHOT_CLASS
#                   VolumeSnapshotClass used to preserve the source PVC
#                   (default: openebs-zfs-snapshot-retain)
#   YES             set to 1 to skip the confirmation prompt
#
# Ctrl+C at any point exits cleanly; if the workload was already scaled to 0,
# it's scaled back to its original replica count on exit.

set -euo pipefail

SCALED_DOWN=0
original_replicas=1
WORKLOAD="${WORKLOAD:-}"
trigger_requested=0
trigger_completed=0
trigger_cleaned=0
manual_was_absent=0

on_exit() {
    local code=$?
    trap - EXIT INT TERM
    if [[ $code -ne 0 && $SCALED_DOWN -eq 1 && -n "$WORKLOAD" ]]; then
        echo >&2
        echo "==> Aborted; scaling ${WORKLOAD} back to ${original_replicas}" >&2
        kubectl -n "$NS" scale "$WORKLOAD" --replicas="$original_replicas" 2>/dev/null || true
    fi
    if [[ $trigger_requested -eq 1 && $trigger_cleaned -eq 0 && $manual_was_absent -eq 1 ]]; then
        echo "WARNING: RD/${RD} manual trigger ${restore_trigger} retained: restore and trigger cleanup were not completed." >&2
        echo "    Inspect the mover and replacement PVC; restore the original restoreAsOf before removing spec.trigger.manual." >&2
    fi
    exit $code
}
on_interrupt() {
    echo >&2
    echo "Interrupted." >&2
    exit 130
}
trap on_exit EXIT
trap on_interrupt INT TERM

APP="${1:?APP is required (e.g. minecraft)}"
ARG2="${2:-}"
NS="${NS:-default}"
TIMEOUT="${TIMEOUT:-5m}"
RESTIC_IMAGE="${RESTIC_IMAGE:-quay.io/backube/volsync:0.15.0}"
ROLLBACK_SNAPSHOT_CLASS="${ROLLBACK_SNAPSHOT_CLASS:-openebs-zfs-snapshot-retain}"
FLUX_KS="${FLUX_KS-${APP}}"
RD="${APP}-dst"
rollback_snapshot="${APP}-pre-restore-$(date -u +%Y%m%d%H%M%S)"

# --- pick or use given timestamp -------------------------------------------

# Hardened pod spec for the ephemeral picker — restricted PSA needs all of
# these set explicitly, otherwise the pod is rejected outright.
read -r -d '' PICKER_OVERRIDES <<JSON || true
{
    "spec": {
        "containers": [{
            "name": "r",
            "image": "${RESTIC_IMAGE}",
            "stdin": true,
            "envFrom": [{"secretRef": {"name": "volsync-${APP}"}}],
            "command": ["restic", "snapshots", "--no-cache", "--no-lock", "--json"],
            "securityContext": {
                "runAsNonRoot": true,
                "runAsUser": 1000,
                "allowPrivilegeEscalation": false,
                "readOnlyRootFilesystem": true,
                "capabilities": {"drop": ["ALL"]},
                "seccompProfile": {"type": "RuntimeDefault"}
            }
        }]
    }
}
JSON

RESTORE_AS_OF=""
case "$ARG2" in
    --pick)
        echo "==> Listing restic snapshots from volsync-${APP}"
        snapshots_json=$(kubectl -n "$NS" run "volsync-pick-${APP}-$$" \
            --image="$RESTIC_IMAGE" --restart=Never --rm -i --quiet \
            --overrides="$PICKER_OVERRIDES" \
            -- /bin/true)
        # Trim sub-second precision for display; keep full string for restic.
        mapfile -t lines < <(jq -r 'sort_by(.time) | to_entries[] |
            "\(.key)\t\(.value.time)\t\(.value.id[0:8])\t\((.value.tags // []) | join(","))"' <<<"$snapshots_json")
        if [[ ${#lines[@]} -eq 0 ]]; then
            echo "ERROR: no snapshots in volsync-${APP}" >&2
            exit 1
        fi
        printf '%5s  %-20s  %-10s  %s\n' "#" "TIME (UTC)" "SHORT-ID" "TAGS"
        for line in "${lines[@]}"; do
            IFS=$'\t' read -r idx t id tags <<<"$line"
            printf '%5s  %-20s  %-10s  %s\n' "$idx" "${t:0:19}Z" "$id" "$tags"
        done
        read -rp "Pick a snapshot (#, or empty for newest): " choice
        if [[ -n "$choice" ]]; then
            RESTORE_AS_OF=$(jq -r --argjson i "$choice" 'sort_by(.time)[$i].time' <<<"$snapshots_json")
            [[ -z "$RESTORE_AS_OF" || "$RESTORE_AS_OF" == "null" ]] && { echo "ERROR: bad selection" >&2; exit 1; }
            echo "    restoring as of ${RESTORE_AS_OF}"
        fi
        ;;
    "")
        : # newest
        ;;
    *)
        RESTORE_AS_OF="$ARG2"
        echo "==> Point-in-time restore: ${RESTORE_AS_OF}"
        ;;
esac

# --- resolve workload to scale --------------------------------------------

if [[ -z "${WORKLOAD:-}" ]]; then
    if kubectl -n "$NS" get "deployment/${APP}" >/dev/null 2>&1; then
        WORKLOAD="deployment/${APP}"
    elif kubectl -n "$NS" get "statefulset/${APP}" >/dev/null 2>&1; then
        WORKLOAD="statefulset/${APP}"
    else
        echo "ERROR: no deployment/${APP} or statefulset/${APP} in ns ${NS}; set WORKLOAD=" >&2
        exit 1
    fi
fi
echo "==> Workload: ${WORKLOAD} (ns: ${NS})"
original_replicas=$(kubectl -n "$NS" get "$WORKLOAD" -o jsonpath='{.spec.replicas}')
[[ -z "$original_replicas" ]] && original_replicas=1
echo "    current replicas: ${original_replicas}"

# --- bump RD trigger ------------------------------------------------------

# RD may be missing if a prior run aborted mid-flight; let flux re-create it.
if ! kubectl -n "$NS" get replicationdestination "$RD" >/dev/null 2>&1; then
    echo "==> RD/${RD} missing; reconciling flux ks/${FLUX_KS:-$APP}"
    if [[ -n "$FLUX_KS" ]] && command -v flux >/dev/null 2>&1; then
        flux reconcile ks "$FLUX_KS" -n "$NS" --with-source
    fi
    until kubectl -n "$NS" get replicationdestination "$RD" >/dev/null 2>&1; do
        sleep 3
    done
fi

echo "==> Patching RD/${RD} to refresh latestImage"
restore_trigger="refresh-$(date +%s)-$$-${RANDOM}"
original_rd=$(kubectl -n "$NS" get replicationdestination "$RD" -o json)
manual_was_absent=$(jq -r 'if ((.spec.trigger // {}) | has("manual")) then 0 else 1 end' <<<"$original_rd")
rd_uid=$(jq -r '.metadata.uid' <<<"$original_rd")
prev=$(jq -r '.status.latestImage.name // ""' <<<"$original_rd")
patch=$(jq -nc --arg trigger "$restore_trigger" --arg before "$RESTORE_AS_OF" \
    '{spec: {trigger: {manual: $trigger}, restic: {
        restoreAsOf: (if $before == "" then null else $before end)}}}')
# A lost patch response can still have started a mover; retain its trigger on failure.
trigger_requested=1
kubectl -n "$NS" patch replicationdestination "$RD" --type=merge -p "$patch"

echo "==> Waiting for new latestImage (was: ${prev:-<none>})"
deadline=$(( $(date +%s) + 600 ))
while [[ $(date +%s) -lt $deadline ]]; do
    observed=$(kubectl -n "$NS" get replicationdestination "$RD" -o json)
    if ! jq -e --arg uid "$rd_uid" --arg token "$restore_trigger" \
        '.metadata.uid == $uid and .spec.trigger.manual == $token' <<<"$observed" >/dev/null; then
        echo "ERROR: RD or manual trigger changed during restore; refusing PVC replacement" >&2
        exit 1
    fi
    if jq -e --arg token "$restore_trigger" --arg previous "$prev" '
        .status.lastManualSync == $token and
        .status.latestMoverStatus.result == "Successful" and
        ((.status.lastSyncStartTime // "") == "") and
        ([.status.conditions[]? | select(.type == "Synchronizing" and .status == "True")] | length == 0) and
        ((.status.latestImage.name // "") != "") and
        .status.latestImage.name != $previous' <<<"$observed" >/dev/null; then
        trigger_completed=1
        current=$(jq -r '.status.latestImage.name' <<<"$observed")
        break
    fi
    sleep 5
done
if [[ $trigger_completed -ne 1 ]]; then
    echo "ERROR: own restore did not complete successfully within 10m" >&2
    exit 1
fi
echo "    new latestImage: ${current}"

# --- confirm before mutating PVC + workload -------------------------------

if [[ "${YES:-0}" != "1" ]]; then
    echo
    echo "About to:"
    echo "  - scale ${WORKLOAD} in ns/${NS} from ${original_replicas} → 0"
    echo "  - preserve pvc/${APP} as volumesnapshot/${rollback_snapshot}"
    echo "  - delete pvc/${APP} in ns/${NS}"
    if [[ -n "$FLUX_KS" ]] && command -v flux >/dev/null 2>&1; then
        echo "  - reconcile flux ks/${FLUX_KS} so it re-creates the PVC"
    fi
    echo "  - wait for the populator to bind the new PVC from latestImage"
    echo "  - scale ${WORKLOAD} back to ${original_replicas}"
    read -rp "Continue? [y/N] " confirm
    case "${confirm,,}" in
        y|yes) ;;
        *) echo "Aborted." >&2; exit 1 ;;
    esac
fi

# --- preserve, replace, and rebind ----------------------------------------

echo "==> Scaling ${WORKLOAD} to 0"
SCALED_DOWN=1
kubectl -n "$NS" scale "$WORKLOAD" --replicas=0
kubectl -n "$NS" wait --for=delete pod \
    -l "app.kubernetes.io/instance=${APP}" \
    --timeout="$TIMEOUT" 2>/dev/null || true

echo "==> Preserving PVC/${APP} as VolumeSnapshot/${rollback_snapshot}"
kubectl -n "$NS" create -f - <<EOF
apiVersion: snapshot.storage.k8s.io/v1
kind: VolumeSnapshot
metadata:
    name: ${rollback_snapshot}
    labels:
        app.kubernetes.io/name: ${APP}
        app.kubernetes.io/managed-by: volsync-restore
spec:
    volumeSnapshotClassName: ${ROLLBACK_SNAPSHOT_CLASS}
    source:
        persistentVolumeClaimName: ${APP}
EOF
kubectl -n "$NS" wait --for=jsonpath='{.status.readyToUse}'=true \
    "volumesnapshot/${rollback_snapshot}" --timeout="$TIMEOUT"

echo "==> Deleting PVC/${APP} (if present)"
kubectl -n "$NS" delete pvc "$APP" --ignore-not-found --wait=true --timeout="$TIMEOUT"

if [[ -n "$FLUX_KS" ]] && command -v flux >/dev/null 2>&1; then
    echo "==> Reconciling flux ks/${FLUX_KS} to re-create the PVC"
    flux reconcile ks "$FLUX_KS" -n "$NS" --with-source
else
    echo "==> Skipping flux reconcile; relying on its next interval to re-create PVC"
fi

echo "==> Waiting for PVC/${APP} to be re-created and Bound"
recreate_deadline=$(( $(date +%s) + 600 ))
while ! kubectl -n "$NS" get pvc "$APP" >/dev/null 2>&1; do
    [[ $(date +%s) -gt $recreate_deadline ]] && { echo "ERROR: PVC not re-created within 10m" >&2; exit 1; }
    sleep 3
done
kubectl -n "$NS" wait --for=jsonpath='{.status.phase}'=Bound \
    "pvc/${APP}" --timeout="$TIMEOUT"

echo "==> Scaling ${WORKLOAD} back to ${original_replicas}"
kubectl -n "$NS" scale "$WORKLOAD" --replicas="$original_replicas"
SCALED_DOWN=0

# Keep manual precedence over cron through confirmation and PVC binding so a
# scheduled refresh cannot replace the selected historical image mid-restore.
if [[ $manual_was_absent -eq 1 ]]; then
    cleanup=$(jq -nc --arg uid "$rd_uid" --arg token "$restore_trigger" \
        --arg before "$RESTORE_AS_OF" --argjson original "$original_rd" '
        ($original.spec.restic // {}) as $restic |
        ($restic + (if $before == "" then {} else {restoreAsOf:$before} end) |
            if $before == "" then del(.restoreAsOf) else . end) as $expected |
        [
            {op:"test", path:"/metadata/uid", value:$uid},
            {op:"test", path:"/spec/trigger/manual", value:$token},
            {op:"test", path:"/spec/restic", value:$expected}
        ] +
        (if ($restic | has("restoreAsOf")) then
            [{op:"add", path:"/spec/restic/restoreAsOf", value:$restic.restoreAsOf}]
            elif $before != "" then
            [{op:"remove", path:"/spec/restic/restoreAsOf"}]
            else [] end) +
        [{op:"remove", path:"/spec/trigger/manual"}]')
    if ! kubectl -n "$NS" patch replicationdestination "$RD" --type=json -p "$cleanup"; then
        echo "ERROR: trigger cleanup conflicted or failed; inspect RD/${RD} before resuming its schedule." >&2
        exit 1
    fi
    trigger_cleaned=1
fi

echo "==> Waiting for ${WORKLOAD} rollout"
kubectl -n "$NS" rollout status "$WORKLOAD" --timeout="$TIMEOUT"

echo "==> Restore complete."
echo "    Rollback snapshot retained: volumesnapshot/${rollback_snapshot}"
echo "    After application validation, patch its VolumeSnapshotContent deletionPolicy to Delete"
echo "    before deleting the VolumeSnapshot; this also cleans up the backend ZFS snapshot."
