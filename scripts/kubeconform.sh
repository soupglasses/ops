#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Sofie <sofie+git@mailbox.org>
#
# SPDX-License-Identifier: MIT
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

KUBE_VERSION="$(awk '/^rke2_version:/ {split($2, version, "+"); sub(/^v/, "", version[1]); print version[1]}' \
    "$REPO_ROOT/ansible/inventories/home/group_vars/all/main.yml")"
schema_dir="$(mktemp -d)"
trap 'rm -rf "$schema_dir"' EXIT

KUBECONFORM_ARGS=(
    "-kubernetes-version=$KUBE_VERSION"
    "-schema-location=$schema_dir/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json"
    -schema-location=default
    -schema-location='https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'
    -schema-location='https://json.schemastore.org/{{.ResourceKind}}.json'
    -summary
)
# Resolve generic LDAP schemas from the exact Provider package deployed by Flux.
crossplane xpkg get-crds \
    "$REPO_ROOT/kubernetes/apps/identity/ldap-provider/app/provider.yaml" \
    --output-dir "$schema_dir/ldap-crds"
yq eval-all -o=json -I=0 '[.]' \
    "$schema_dir/ldap-crds/"*/*/*.yaml \
    "$REPO_ROOT/kubernetes/apps/identity/ldap-provider/app/oauth2.yaml" \
    "$REPO_ROOT/kubernetes/apps/identity/ldap-provider/app/groups.yaml" \
    "$REPO_ROOT/kubernetes/apps/identity/ldap-provider/app/serviceaccounts.yaml" \
    | python3 "$REPO_ROOT/scripts/kubeconform-local-schemas.py" "$schema_dir"

yq eval-all -o=json -I=0 '[.]' \
    "$REPO_ROOT/packages/provider-jellyfin/package/crds/"*.yaml \
    "$REPO_ROOT/kubernetes/apps/crossplane-system/crossplane/providers/jellyfin/pluginsettings.yaml" \
    | python3 "$REPO_ROOT/scripts/kubeconform-local-schemas.py" "$schema_dir"

# The community catalog omits CRDs themselves; validate those against the
# selected Kubernetes release's own OpenAPI definitions.
if grep -q '^kind: CustomResourceDefinition$' "$@"; then
    curl --fail --silent --show-error --max-time 30 \
        "https://raw.githubusercontent.com/kubernetes/kubernetes/v${KUBE_VERSION}/api/openapi-spec/swagger.json" \
        | yq -e -o=json \
            '.definitions."io.k8s.apiextensions-apiserver.pkg.apis.apiextensions.v1.CustomResourceDefinition" as $root | $root * {"definitions": .definitions}' \
        >"$schema_dir/customresourcedefinition_v1.json"
    KUBECONFORM_ARGS+=("-schema-location=$schema_dir/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json")
fi

# This CRD is missing from the catalog. Extract its schema from the versioned
# upstream definition instead of maintaining a copy in this repository.
if grep -q '^kind: CiliumGatewayClassConfig$' "$@"; then
    # Match the Cilium bundled by the inventory's RKE2 release.
    RKE2_VERSION="$(yq -r '.rke2_version' "$REPO_ROOT/ansible/inventories/home/group_vars/all/main.yml")"
    CILIUM_CHART_VERSION="$(curl --fail --silent --show-error --max-time 30 \
        "https://raw.githubusercontent.com/rancher/rke2/${RKE2_VERSION}/charts/chart_versions.yaml" \
        | yq -er '.charts[] | select(.filename == "/charts/rke2-cilium.yaml") | .version')"
    CILIUM_SCHEMA_VERSION="v$(curl --fail --silent --show-error --max-time 30 \
        "https://raw.githubusercontent.com/rancher/rke2-charts/main/assets/rke2-cilium/rke2-cilium-${CILIUM_CHART_VERSION}.tgz" \
        | tar -xzO rke2-cilium/Chart.yaml | yq -er '.appVersion')"
    curl --fail --silent --show-error --max-time 30 \
        "https://raw.githubusercontent.com/cilium/cilium/${CILIUM_SCHEMA_VERSION}/pkg/k8s/apis/cilium.io/client/crds/v2alpha1/ciliumgatewayclassconfigs.yaml" \
        | yq -e -o=json '.spec.versions[] | select(.name == "v2alpha1") | .schema.openAPIV3Schema' \
        >"$schema_dir/ciliumgatewayclassconfig_v2alpha1.json"
    KUBECONFORM_ARGS+=("-schema-location=$schema_dir/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json")
fi

fail=0
for f in "$@"; do
    if ! flux envsubst <"$f" | kubeconform "${KUBECONFORM_ARGS[@]}" - 2>&1 \
            | sed "s|^stdin |$f |"; then
        fail=1
    fi
done
exit "$fail"
