#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Sofie <sofie+git@mailbox.org>
#
# SPDX-License-Identifier: MIT
#
# Force a volsync PVC to restore from restic.
#
# A destination latestImage may be stale until its next explicit restore. Recreating the PVC alone would restore that stale image. This script
# bumps the RD trigger, waits for the mover to refresh latestImage, scales the
# workload to 0, preserves and replaces the PVC with controllers held, then
# verifies application contents before releasing the recovery holds.
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
#   FLUX_KS         Kustomization name to suspend during recovery
#                   (default: ${APP} in ${NS}). Set empty to skip.
#   TIMEOUT         kubectl wait timeout (default: 5m)
#   RESTIC_IMAGE    image with restic+jq for the picker
#                   (default: running volsync controller image)
#   ROLLBACK_SNAPSHOT_CLASS
#                   VolumeSnapshotClass used to preserve the source PVC
#                   (default: openebs-zfs-snapshot-retain)
#   YES             set to 1 to skip the confirmation prompt
#   VERIFY_COMMAND required application-level check, run after rollout and before
#                   releasing recovery holds (APP, NS and WORKLOAD are exported)
#
# An interrupted or failed restore leaves the workload stopped. Inspect the
# retained data snapshot before explicitly resuming writes.

set -euo pipefail

SCALED_DOWN=0
original_replicas=1
WORKLOAD="${WORKLOAD:-}"
trigger_requested=0
trigger_completed=0
trigger_cleaned=0
manual_was_absent=0
held_controls=()
held_fields=()
lock_cleaned=0

on_exit() {
    local code=$?
    trap - EXIT INT TERM
    if [[ $code -ne 0 && $SCALED_DOWN -eq 1 && -n "$WORKLOAD" ]]; then
        if ! kubectl -n "$NS" scale "$WORKLOAD" --replicas=0; then
            echo "ERROR: could not confirm writers stopped; stop ${WORKLOAD} before further recovery work." >&2
        fi
        echo >&2
        echo "ERROR: restore failed; ${WORKLOAD} remains stopped to protect application data." >&2
        echo "    Inspect PVC/${APP} and retained VolumeSnapshot/${rollback_snapshot} before resuming writes." >&2
    fi
    if [[ $trigger_requested -eq 1 && $trigger_cleaned -eq 0 && $manual_was_absent -eq 1 ]]; then
        echo "WARNING: RD/${RD} manual trigger ${restore_trigger} retained: restore and trigger cleanup were not completed." >&2
        echo "    Inspect the mover and replacement PVC; restore the original restoreAsOf before removing spec.trigger.manual." >&2
    fi
    if [[ $code -ne 0 && $trigger_requested -eq 1 && $lock_cleaned -eq 0 ]]; then
        echo "    RD/${RD} recovery lock retained: ${restore_trigger}. Inspect it before another restore." >&2
    fi
    if [[ $code -ne 0 && ${#held_controls[@]} -gt 0 ]]; then
        echo "    Recovery holds retained: ${held_controls[*]}. Inspect data before releasing them." >&2
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
RESTIC_IMAGE="${RESTIC_IMAGE:-}"
ROLLBACK_SNAPSHOT_CLASS="${ROLLBACK_SNAPSHOT_CLASS:-openebs-zfs-snapshot-retain}"
FLUX_KS="${FLUX_KS-${APP}}"
RD="${APP}-dst"
rollback_snapshot="${APP}-pre-restore-$(date -u +%Y%m%d%H%M%S)"
for name in "$APP" "$NS"; do
    [[ "$name" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]] || { echo "ERROR: invalid application or namespace name" >&2; exit 1; }
done
# Only a declared backup source authorizes this recovery path. In particular,
# intentionally unbacked workloads must not acquire recovery resources here.
source_pvc=$(kubectl -n "$NS" get replicationsource "$APP" -o jsonpath='{.spec.sourcePVC}')
[[ "$source_pvc" == "$APP" ]] || { echo "ERROR: no declared backup source for PVC/${APP}" >&2; exit 1; }
policy=$(kubectl get volumesnapshotclass "$ROLLBACK_SNAPSHOT_CLASS" -o jsonpath='{.deletionPolicy}')
[[ "$policy" == Retain ]] || { echo "ERROR: rollback snapshots require a Retain snapshot class" >&2; exit 1; }

# --- pick or use given timestamp -------------------------------------------

RESTORE_AS_OF=""
case "$ARG2" in
    --pick)
        if [[ -z "$RESTIC_IMAGE" ]]; then
            RESTIC_IMAGE=$(kubectl -n volsync-system get deployment volsync -o jsonpath='{.spec.template.spec.containers[0].image}')
            [[ -n "$RESTIC_IMAGE" ]] || { echo "ERROR: cannot determine running VolSync image; set RESTIC_IMAGE" >&2; exit 1; }
        fi
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
workload_json=$(kubectl -n "$NS" get "$WORKLOAD" -o json)
selector=$(jq -er '.spec.selector.matchLabels | to_entries | map(.key + "=" + .value) | join(",") | select(length > 0)' <<<"$workload_json")
[[ -n "${VERIFY_COMMAND:-}" ]] || { echo "ERROR: VERIFY_COMMAND must check restored application contents" >&2; exit 1; }
if jq -e '(.spec.selector.matchExpressions // [] | length) > 0' <<<"$workload_json" >/dev/null; then
    echo "ERROR: workload selector expressions require an explicit recovery plan." >&2
    exit 1
fi

original_pvc=$(kubectl -n "$NS" get pvc "$APP" -o json)
original_pvc_uid=$(jq -er '.metadata.uid' <<<"$original_pvc")
if ! jq -e --arg destination "$RD" '.spec.dataSourceRef.kind == "ReplicationDestination" and .spec.dataSourceRef.name == $destination' <<<"$original_pvc" >/dev/null; then
    echo "ERROR: PVC does not declare the expected recovery destination" >&2
    exit 1
fi

# --- bump RD trigger ------------------------------------------------------

# RD may be missing if a prior run aborted mid-flight; let flux re-create it.
if ! kubectl -n "$NS" get replicationdestination "$RD" >/dev/null 2>&1; then
    echo "==> RD/${RD} missing; reconciling flux ks/${FLUX_KS:-$APP}"
    if [[ -n "$FLUX_KS" ]] && command -v flux >/dev/null 2>&1; then
        flux reconcile ks "$FLUX_KS" -n "$NS" --with-source
    fi
    destination_deadline=$(( $(date +%s) + 600 ))
    until kubectl -n "$NS" get replicationdestination "$RD" >/dev/null 2>&1; do
        [[ $(date +%s) -lt $destination_deadline ]] || { echo "ERROR: restore destination was not recreated" >&2; exit 1; }
        sleep 3
    done
fi

echo "==> Patching RD/${RD} to refresh latestImage"
restore_trigger="refresh-$(date +%s)-$$-${RANDOM}"
original_rd=$(kubectl -n "$NS" get replicationdestination "$RD" -o json)
if ! jq -e '
    ((.metadata.annotations["ops.soupglasses.dev/recovery-operation"] // "") == "") and
    (.spec.paused != true) and
    ((.status.lastSyncStartTime // "") == "") and
    ([.status.conditions[]? | select(.type == "Synchronizing" and .status == "True")] | length == 0) and
    (((.spec.trigger.manual // "") == "") or (.spec.trigger.manual == .status.lastManualSync))
    ' <<<"$original_rd" >/dev/null; then
    echo "ERROR: restore destination is paused or has an unfinished request; refusing to replace its trigger" >&2
    exit 1
fi
manual_was_absent=$(jq -r 'if ((.spec.trigger // {}) | has("manual")) then 0 else 1 end' <<<"$original_rd")
rd_uid=$(jq -r '.metadata.uid' <<<"$original_rd")
prev=$(jq -r '.status.latestImage.name // ""' <<<"$original_rd")
patch=$(jq -nc --arg trigger "$restore_trigger" --arg before "$RESTORE_AS_OF" --argjson original "$original_rd" \
    '{metadata: {resourceVersion: $original.metadata.resourceVersion, annotations:{"ops.soupglasses.dev/recovery-operation":$trigger}}, spec: {trigger: {manual: $trigger}, restic: {
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
    echo "  - hold GitOps, backup and application controllers during replacement"
    echo "  - wait for the populator to bind the new PVC from latestImage"
    echo "  - scale ${WORKLOAD} back to ${original_replicas}"
    read -rp "Continue? [y/N] " confirm
    case "${confirm,,}" in
        y|yes) ;;
        *) echo "Aborted." >&2; exit 1 ;;
    esac
fi

# --- preserve, replace, and rebind ----------------------------------------

# Hold the controllers that could otherwise restart writers during recovery.
# Already-paused controls retain their original state on successful completion.
hold_control() {
    local resource=$1 field=$2 object patch
    object=$(kubectl -n "$NS" get "$resource" -o json)
    if [[ -n $(jq -r '.metadata.annotations["ops.soupglasses.dev/recovery-operation"] // ""' <<<"$object") ]]; then
        echo "ERROR: another recovery holds $resource" >&2
        exit 1
    fi
    if jq -e '[.status.conditions[]? | select(.type == "Reconciling" and .status == "True")] | length > 0' <<<"$object" >/dev/null; then
        echo "ERROR: $resource is reconciling; wait for it to finish" >&2
        exit 1
    fi
    if [[ $(jq -r --arg field "$field" '.spec[$field] // false' <<<"$object") != true ]]; then
        patch=$(jq -nc --arg field "$field" --arg token "$restore_trigger" --argjson original "$object" \
            '{metadata:{resourceVersion:$original.metadata.resourceVersion,annotations:{"ops.soupglasses.dev/recovery-operation":$token}},spec:{($field):true}}')
        # Record before patching: a lost response must not be treated as no hold.
        held_controls+=("$resource")
        held_fields+=("$field")
        kubectl -n "$NS" patch "$resource" --type=merge -p "$patch"
    fi
}
source_state=$(kubectl -n "$NS" get "replicationsource/$APP" -o json)
if ! jq -e '((.status.lastSyncStartTime // "") == "") and ([.status.conditions[]? | select(.type == "Synchronizing" and .status == "True")] | length == 0)' <<<"$source_state" >/dev/null; then
    echo "ERROR: backup is active; wait for it to finish before replacing its source PVC" >&2
    exit 1
fi
if [[ -n "$FLUX_KS" ]]; then
    hold_control "kustomization.kustomize.toolkit.fluxcd.io/$FLUX_KS" suspend
fi
hold_control "replicationsource/$APP" paused
# Pausing does not cancel an already-started backup. Recheck after the hold.
source_state=$(kubectl -n "$NS" get "replicationsource/$APP" -o json)
if ! jq -e '((.status.lastSyncStartTime // "") == "") and ([.status.conditions[]? | select(.type == "Synchronizing" and .status == "True")] | length == 0)' <<<"$source_state" >/dev/null; then
    echo "ERROR: backup started while taking recovery holds; wait for it to finish" >&2
    exit 1
fi
controller=$(jq -r '.metadata.ownerReferences[]? | select(.controller == true) | .kind + "/" + .name' <<<"$workload_json")
helm_object=$workload_json
if [[ -n "$controller" ]]; then
    case "$controller" in
        VMSingle/*)
            controller="vmsingle.operator.victoriametrics.com/${controller#*/}"
            hold_control "$controller" paused
            helm_object=$(kubectl -n "$NS" get "$controller" -o json)
            ;;
        *) echo "ERROR: unsupported workload controller: $controller; recovery holds retained" >&2; exit 1 ;;
    esac
fi
release=$(jq -r '.metadata.annotations["meta.helm.sh/release-name"] // ""' <<<"$helm_object")
if [[ -n "$release" ]]; then
    hold_control "helmrelease.helm.toolkit.fluxcd.io/$release" suspend
fi

echo "==> Scaling ${WORKLOAD} to 0"
SCALED_DOWN=1
kubectl -n "$NS" scale "$WORKLOAD" --replicas=0
pods=$(kubectl -n "$NS" get pods -l "$selector" -o jsonpath='{.items[*].metadata.name}')
if [[ -n "$pods" ]]; then
    kubectl -n "$NS" wait --for=delete pod -l "$selector" --timeout="$TIMEOUT"
fi

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

current_uid=$(kubectl -n "$NS" get pvc "$APP" -o jsonpath='{.metadata.uid}')
[[ "$current_uid" == "$original_pvc_uid" ]] || { echo "ERROR: source PVC changed; refusing deletion" >&2; exit 1; }
echo "==> Deleting PVC/${APP} (if present)"
kubectl -n "$NS" delete pvc "$APP" --ignore-not-found --wait=true --timeout="$TIMEOUT"

# Recreate the declared claim directly while Flux and Helm remain suspended;
# reconciling either controller here could restart writers before validation.
jq '{apiVersion:"v1",kind:"PersistentVolumeClaim",metadata:{name:.metadata.name,namespace:.metadata.namespace,labels:.metadata.labels},spec:.spec} | del(.spec.volumeName)' <<<"$original_pvc" \
    | kubectl -n "$NS" create -f -

echo "==> Waiting for PVC/${APP} to be re-created and Bound"
recreate_deadline=$(( $(date +%s) + 600 ))
while ! kubectl -n "$NS" get pvc "$APP" >/dev/null 2>&1; do
    [[ $(date +%s) -gt $recreate_deadline ]] && { echo "ERROR: PVC not re-created within 10m" >&2; exit 1; }
    sleep 3
done
kubectl -n "$NS" wait --for=jsonpath='{.status.phase}'=Bound \
    "pvc/${APP}" --timeout="$TIMEOUT"

# Retain recovery ownership through confirmation and PVC binding.
cleanup=$(jq -nc --arg uid "$rd_uid" --arg token "$restore_trigger" \
        --arg before "$RESTORE_AS_OF" --argjson original "$original_rd" --argjson remove_manual "$manual_was_absent" '
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
        (if $remove_manual == 1 then [{op:"remove", path:"/spec/trigger/manual"}] else [] end)')
    if ! kubectl -n "$NS" patch replicationdestination "$RD" --type=json -p "$cleanup"; then
        echo "ERROR: trigger cleanup conflicted or failed; inspect RD/${RD} before resuming its schedule." >&2
        exit 1
    fi
trigger_cleaned=1

echo "==> Scaling ${WORKLOAD} back to ${original_replicas}"
kubectl -n "$NS" scale "$WORKLOAD" --replicas="$original_replicas"

echo "==> Waiting for ${WORKLOAD} rollout"
if ! kubectl -n "$NS" rollout status "$WORKLOAD" --timeout="$TIMEOUT"; then
    kubectl -n "$NS" scale "$WORKLOAD" --replicas=0
    exit 1
fi
echo "==> Verifying restored application contents"
export APP NS WORKLOAD
if ! bash -c "$VERIFY_COMMAND"; then
    kubectl -n "$NS" scale "$WORKLOAD" --replicas=0
    echo "ERROR: application recovery verification failed; recovery holds and rollback snapshot retained" >&2
    exit 1
fi
SCALED_DOWN=0
for ((index=${#held_controls[@]}-1; index>=0; index--)); do
    resource=${held_controls[$index]}
    field=${held_fields[$index]}
    patch=$(jq -nc --arg field "$field" --arg token "$restore_trigger" '[
        {op:"test",path:"/metadata/annotations/ops.soupglasses.dev~1recovery-operation",value:$token},
        {op:"test",path:("/spec/"+$field),value:true},
        {op:"replace",path:("/spec/"+$field),value:false},
        {op:"remove",path:"/metadata/annotations/ops.soupglasses.dev~1recovery-operation"}]')
    kubectl -n "$NS" patch "$resource" --type=json -p "$patch"
done
held_controls=()
lock_cleanup=$(jq -nc --arg token "$restore_trigger" '[
    {op:"test",path:"/metadata/annotations/ops.soupglasses.dev~1recovery-operation",value:$token},
    {op:"remove",path:"/metadata/annotations/ops.soupglasses.dev~1recovery-operation"}]')
kubectl -n "$NS" patch replicationdestination "$RD" --type=json -p "$lock_cleanup"
lock_cleaned=1

echo "==> Restore complete."
echo "    Rollback snapshot retained: volumesnapshot/${rollback_snapshot}"
echo "    After application validation, patch its VolumeSnapshotContent deletionPolicy to Delete"
echo "    before deleting the VolumeSnapshot; this also cleans up the backend ZFS snapshot."
