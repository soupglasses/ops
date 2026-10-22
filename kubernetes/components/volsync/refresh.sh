#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: 0BSD
set -euo pipefail

: "$APP" "$NS"
[[ -n "$APP" && -n "$NS" ]] || { echo "ERROR: APP and NS are required" >&2; exit 1; }
key=ops.soupglasses.dev/recovery-operation
checkpoint_key=ops.soupglasses.dev/restore-checkpoint
destination="$APP-dst"
refresh_timeout=1500
poll_seconds=5
if [[ -v REFRESH_TIMEOUT_SECONDS ]]; then refresh_timeout=$REFRESH_TIMEOUT_SECONDS; fi
if [[ -v REFRESH_POLL_SECONDS ]]; then poll_seconds=$REFRESH_POLL_SECONDS; fi
deadline=$((SECONDS + refresh_timeout))

# The pinned client's timeout override bypasses in-cluster configuration. Bound
# each process instead, preserving native service-account token handling.
kubectl() {
    timeout 20s kubectl "$@"
}

read_destination() {
    kubectl -n "$NS" get replicationdestination "$destination" -o json
}
idle='((.status.lastSyncStartTime // "") == "") and
    ([.status.conditions[]? | select(.type == "Synchronizing" and .status == "True")] | length == 0) and
    (((.spec.trigger.manual // "") == "") or (.spec.trigger.manual == .status.lastManualSync))'
source=$(kubectl -n "$NS" get replicationsource "$APP" -o json)
if ! jq -e --arg app "$APP" "($idle) and (.spec.paused != true) and
    (.spec.sourcePVC == \$app) and (.spec.restic != null) and (.status.latestMoverStatus.result == \"Successful\") and
    ((.status.lastSyncTime // \"\") != \"\")" <<<"$source" >/dev/null; then
    echo 'Backup is paused, unfinished or unsuccessful; no restore image requested.'
    exit 0
fi
checkpoint=$(jq -r '.status.lastSyncTime' <<<"$source")
original=$(read_destination)
lock=$(jq -r --arg key "$key" '.metadata.annotations[$key] // ""' <<<"$original")
if [[ -n "$lock" && "$lock" != backup-refresh-* ]]; then
    echo 'Explicit recovery holds the destination; no automatic refresh.'
    exit 0
fi
if ! jq -e '(.spec.paused != true) and ((.spec.restic.restoreAsOf // "") == "")' <<<"$original" >/dev/null; then
    echo 'Destination is paused or selects historical data; no automatic refresh.'
    exit 0
fi
if [[ -z "$lock" ]]; then
    if ! jq -e "$idle" <<<"$original" >/dev/null; then
        echo 'Destination has unfinished work; no trigger override.'
        exit 0
    fi
    recorded=$(jq -r --arg key "$checkpoint_key" '.metadata.annotations[$key] // ""' <<<"$original")
    if [[ "$recorded" == "$checkpoint" ]] && jq -e '(.status.latestImage.name // "") != ""' <<<"$original" >/dev/null; then
        echo 'Restore image already includes the successful backup checkpoint.'
        exit 0
    fi
    token="backup-refresh-$checkpoint"
    previous=$(jq -r '.status.latestImage.name // ""' <<<"$original")
    patch=$(jq -nc --arg token "$token" --arg key "$key" --arg previous "$previous" --argjson object "$original" '
        {metadata:{resourceVersion:$object.metadata.resourceVersion,annotations:{($key):$token,
            "ops.soupglasses.dev/previous-restore-image":$previous}},spec:{trigger:{manual:$token}}}')
    kubectl -n "$NS" patch replicationdestination "$destination" --type=merge -p "$patch" >/dev/null
else
    # A retried CronJob continues its own pending request, never a new checkpoint.
    token=$lock
    checkpoint=$(jq -nr --arg token "$token" '$token | ltrimstr("backup-refresh-")')
    previous=$(jq -r '.metadata.annotations["ops.soupglasses.dev/previous-restore-image"] // ""' <<<"$original")
fi
uid=$(jq -er '.metadata.uid' <<<"$original")
while (( SECONDS < deadline )); do
    observed=$(read_destination)
    if ! jq -e --arg uid "$uid" --arg key "$key" --arg token "$token" '
        .metadata.uid == $uid and .metadata.annotations[$key] == $token and .spec.trigger.manual == $token
        and (.spec.paused != true) and ((.spec.restic.restoreAsOf // "") == "")' <<<"$observed" >/dev/null; then
        echo 'ERROR: destination ownership or selection changed; refresh lock retained.' >&2
        exit 1
    fi
    if jq -e --arg token "$token" --arg previous "$previous" "($idle) and
        (.status.lastManualSync == \$token) and (.status.latestMoverStatus.result == \"Successful\") and
        ((.status.latestImage.name // \"\") != \"\") and (.status.latestImage.name != \$previous)" <<<"$observed" >/dev/null; then
        patch=$(jq -nc --arg uid "$uid" --arg token "$token" --arg checkpoint "$checkpoint" '
            [{op:"test",path:"/metadata/uid",value:$uid},
                {op:"test",path:"/metadata/annotations/ops.soupglasses.dev~1recovery-operation",value:$token},
                {op:"test",path:"/spec/trigger/manual",value:$token},
                {op:"add",path:"/metadata/annotations/ops.soupglasses.dev~1restore-checkpoint",value:$checkpoint},
                {op:"remove",path:"/metadata/annotations/ops.soupglasses.dev~1previous-restore-image"},
                {op:"remove",path:"/metadata/annotations/ops.soupglasses.dev~1recovery-operation"}]')
        kubectl -n "$NS" patch replicationdestination "$destination" --type=json -p "$patch" >/dev/null
        echo 'Restore image refreshed from the successful backup checkpoint.'
        exit 0
    fi
    sleep "$poll_seconds"
done
echo 'ERROR: restore image did not refresh before timeout; lock retained for diagnosis or retry.' >&2
exit 1
