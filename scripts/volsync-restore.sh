#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Sofie <sofie+git@mailbox.org>
#
# SPDX-License-Identifier: 0BSD
#
# Force a volsync PVC to restore from restic.
#
# Why this exists: components/volsync's RD fires `manual: restore-once` on
# creation and never again, so its .status.latestImage is frozen to whatever
# was in restic at first deploy. Recreating the PVC alone would restore that
# stale frozen image. This script bumps the RD trigger, waits for the mover
# to refresh latestImage, scales the workload to 0, deletes the PVC, kicks
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
#   YES             set to 1 to skip the confirmation prompt
#
# Ctrl+C at any point exits cleanly; if the workload was already scaled to 0,
# it's scaled back to its original replica count on exit.

set -euo pipefail

SCALED_DOWN=0
original_replicas=1
WORKLOAD=""

on_exit() {
    local code=$?
    trap - EXIT INT TERM
    if [[ $code -ne 0 && $SCALED_DOWN -eq 1 && -n "$WORKLOAD" ]]; then
        echo >&2
        echo "==> Aborted; scaling ${WORKLOAD} back to ${original_replicas}" >&2
        kubectl -n "$NS" scale "$WORKLOAD" --replicas="$original_replicas" 2>/dev/null || true
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
FLUX_KS="${FLUX_KS-${APP}}"
RD="${APP}-dst"

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
patch_spec="\"trigger\":{\"manual\":\"refresh-$(date +%s)\"}"
if [[ -n "$RESTORE_AS_OF" ]]; then
    patch_spec+=",\"restic\":{\"restoreAsOf\":\"${RESTORE_AS_OF}\"}"
fi
prev=$(kubectl -n "$NS" get replicationdestination "$RD" \
    -o jsonpath='{.status.latestImage.name}' 2>/dev/null || true)
kubectl -n "$NS" patch replicationdestination "$RD" --type=merge \
    -p "{\"spec\":{${patch_spec}}}"

echo "==> Waiting for new latestImage (was: ${prev:-<none>})"
# We can't `kubectl wait --for=jsonpath=...=<value>` here because we don't
# know the new image name in advance. kubectl wait uses the K8s watch API
# (HTTP server-stream, event-driven) but only for equality matches. A short
# polling loop is fine — the mover takes ~tens of seconds per try.
deadline=$(( $(date +%s) + 600 ))
while [[ $(date +%s) -lt $deadline ]]; do
    current=$(kubectl -n "$NS" get replicationdestination "$RD" \
        -o jsonpath='{.status.latestImage.name}' 2>/dev/null || true)
    if [[ -n "$current" && "$current" != "$prev" ]]; then
        break
    fi
    sleep 5
done
if [[ -z "${current:-}" || "$current" == "$prev" ]]; then
    echo "ERROR: latestImage did not refresh within 10m" >&2
    exit 1
fi
echo "    new latestImage: ${current}"

# --- confirm before mutating PVC + workload -------------------------------

if [[ "${YES:-0}" != "1" ]]; then
    echo
    echo "About to:"
    echo "  - scale ${WORKLOAD} in ns/${NS} from ${original_replicas} → 0"
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

# --- nuke & rebind --------------------------------------------------------

echo "==> Scaling ${WORKLOAD} to 0"
SCALED_DOWN=1
kubectl -n "$NS" scale "$WORKLOAD" --replicas=0
kubectl -n "$NS" wait --for=delete pod \
    -l "app.kubernetes.io/instance=${APP}" \
    --timeout="$TIMEOUT" 2>/dev/null || true

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

echo "==> Waiting for ${WORKLOAD} rollout"
kubectl -n "$NS" rollout status "$WORKLOAD" --timeout="$TIMEOUT"

echo "==> Restore complete."
