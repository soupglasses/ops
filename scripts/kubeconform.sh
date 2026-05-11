#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Sofie <sofie+git@mailbox.org>
#
# SPDX-License-Identifier: 0BSD
#
# Pre-commit kubeconform wrapper.
#
# Pipes each YAML file through `flux envsubst` before validating, so that
# flux postBuild placeholders (e.g. ${VOLSYNC_BOOTSTRAP_UID:=1000}) resolve
# to their defaults instead of being seen as opaque strings — which is the
# only way a typed-int field like runAsUser passes schema checks.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
SETTINGS="$REPO_ROOT/kubernetes/components/common/config/cluster-settings.yaml"

# Real cluster-wide vars come from the same ConfigMap the controller reads.
# yq -o=shell emits KEY='value' lines safe for eval.
eval "$(yq -o=shell '.data' "$SETTINGS")"
export TIMEZONE CLUSTER_NAME DOMAIN IP_ADDRESS S3_ENDPOINT S3_BUCKET

# Placeholder values for per-app and per-secret substitutions. These exist
# only to satisfy schema validation (type + non-empty), not to be correct.
export APP=placeholder
export SECRET_S3_ENDPOINT=s3:placeholder
export SECRET_PASSWORD=placeholder
export SECRET_AWS_ACCESS_KEY_ID=placeholder
export SECRET_AWS_SECRET_ACCESS_KEY=placeholder

KUBECONFORM_ARGS=(
    -kubernetes-version=1.33.2
    -schema-location=default
    -schema-location='https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'
    -schema-location='https://json.schemastore.org/{{.ResourceKind}}.json'
    -summary
)

fail=0
for f in "$@"; do
    if ! flux envsubst <"$f" | kubeconform "${KUBECONFORM_ARGS[@]}" - 2>&1 \
            | sed "s|^stdin |$f |"; then
        fail=1
    fi
done
exit "$fail"
