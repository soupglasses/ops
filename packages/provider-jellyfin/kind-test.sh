#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT
set -euo pipefail
repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
cluster="ops-jellyfin-test-$$"
registry="${cluster}-registry"
work_dir=$(mktemp -d)
export KUBECONFIG="$work_dir/kubeconfig"
package_file=${PROVIDER_PACKAGE:-/tmp/provider-jellyfin.xpkg}
registry_image=registry:3.0.0@sha256:6c5666b861f3505b116bb9aa9b25175e71210414bd010d92035ff64018f9457e
cleanup() {
    result=$?
    trap - EXIT
    if ((result != 0)); then
        kubectl get provider,providerrevision -o yaml >&2 || true
        kubectl get pods --namespace crossplane-system >&2 || true
        kubectl get systemconfigurations --namespace media -o yaml >&2 || true
        kubectl get pluginconfigurations --namespace media -o yaml >&2 || true
        kubectl describe pod --namespace media jellyfin-fixture >&2 || true
        kubectl logs --namespace media jellyfin-fixture --tail=60 >&2 || true
        kubectl logs --namespace crossplane-system -l app.kubernetes.io/name=jellyfin-provider --tail=60 >&2 || true
    fi
    kind delete cluster --name "$cluster" || true
    docker rm -f "$registry" >/dev/null 2>&1 || true
    rm -rf -- "$work_dir"
    exit "$result"
}
trap cleanup EXIT
[[ -s "$package_file" ]]
kind create cluster --name "$cluster" --kubeconfig "$KUBECONFIG" \
    --image kindest/node:v1.34.11@sha256:44e222ee2132dab25ff87301682f89eb82c7880ea3a1bf543bfe9708fd08d67d
if [[ -n "${JELLYFIN_TEST_IMAGE:-}" ]]; then
    kind load docker-image --name "$cluster" "$JELLYFIN_TEST_IMAGE"
fi
docker run -d --name "$registry" --network kind "$registry_image" >/dev/null
registry_ip=$(docker inspect "$registry" --format '{{(index .NetworkSettings.Networks "kind").IPAddress}}')
registry_address="$registry_ip:5000"
# Both the package manager and kubelet pull the OCI package; only this disposable node trusts HTTP.
docker exec "$cluster-control-plane" mkdir -p "/etc/containerd/certs.d/$registry_address"
cat <<CONFIG | docker exec -i "$cluster-control-plane" tee "/etc/containerd/certs.d/$registry_address/hosts.toml" >/dev/null
server = "http://$registry_address"
[host."http://$registry_address"]
    capabilities = ["pull", "resolve"]
CONFIG
crossplane xpkg push --insecure-skip-tls-verify --package-files "$package_file" \
    "$registry_address/provider-jellyfin:v0.1.0"
for namespace in crossplane-system media; do
    kubectl create namespace "$namespace"
    kubectl label namespace "$namespace" \
        pod-security.kubernetes.io/enforce=restricted \
        pod-security.kubernetes.io/enforce-version=v1.34
done
helm upgrade --install crossplane crossplane --repo https://charts.crossplane.io/stable \
    --version 2.4.2 --namespace crossplane-system \
    --set-json 'securityContextCrossplane={"runAsNonRoot":true,"allowPrivilegeEscalation":false,"capabilities":{"drop":["ALL"]},"seccompProfile":{"type":"RuntimeDefault"}}' \
    --set-json 'securityContextRBACManager={"runAsNonRoot":true,"allowPrivilegeEscalation":false,"capabilities":{"drop":["ALL"]},"seccompProfile":{"type":"RuntimeDefault"}}' \
    --wait --timeout 4m
# Rewrite the package before creation so the test never contacts the production registry.
kubectl create --dry-run=client --filename "$repo_dir/kubernetes/apps/crossplane-system/crossplane/providers/jellyfin/provider.yaml" \
    --output json >"$work_dir/provider.json"
python3 - "$work_dir/provider.json" "$registry_address" <<'PY'
import json
import sys
with open(sys.argv[1]) as source:
    remaining = source.read().strip()
items = []
decoder = json.JSONDecoder()
while remaining:
    item, end = decoder.raw_decode(remaining)
    items.append(item)
    remaining = remaining[end:].lstrip()
resources = {'apiVersion': 'v1', 'kind': 'List', 'items': items}
for item in items:
    if item['kind'] == 'Provider':
        item['spec']['package'] = sys.argv[2] + '/provider-jellyfin:v0.1.0'
with open(sys.argv[1], 'w') as destination:
    json.dump(resources, destination)
PY
kubectl apply --filename "$work_dir/provider.json"
kubectl wait provider/jellyfin --for=condition=Healthy --timeout=180s
kubectl rollout status --namespace crossplane-system \
    deployment/"$(kubectl get provider jellyfin --output jsonpath='{.status.currentRevision}')" --timeout=90s
identity=system:serviceaccount:crossplane-system:jellyfin-provider
failed=0
expect_permission() {
    expected=$1
    shift
    actual=$(kubectl auth can-i --as="$identity" "$@" 2>/dev/null || true)
    if [[ "$actual" != "$expected" ]]; then
        printf 'ERROR: expected %s for %s; got %s\n' "$expected" "$*" "$actual" >&2
        failed=1
    fi
}
# Native Crossplane providers receive cluster-wide Secret permissions, independently of CRD scope.
for verb in get list create update delete; do
    expect_permission yes "$verb" secrets --all-namespaces
done
identity=system:serviceaccount:crossplane-system:crossplane
for resource in secrets pluginconfigurations.jellyfin.ops.finnes.dev; do
    for verb in get list watch create update patch delete; do
        expect_permission yes "$verb" "$resource" --namespace media
    done
done
for resource in providerconfigs brandingconfigurations libraries networkingconfigurations \
    pluginconfigurations pluginrepositories plugins scheduledtasks systemconfigurations; do
    scope=$(kubectl get crd "$resource.jellyfin.ops.finnes.dev" --output jsonpath='{.spec.scope}')
    if [[ "$scope" != Namespaced ]]; then
        printf 'ERROR: expected namespaced %s API; got %s\n' "$resource" "$scope" >&2
        failed=1
    fi
done
revision=$(kubectl get provider jellyfin --output jsonpath='{.status.currentRevision}')
watch_namespace=$(kubectl get deployment "$revision" --namespace crossplane-system \
    --output jsonpath='{.spec.template.spec.containers[?(@.name=="package-runtime")].env[?(@.name=="WATCH_NAMESPACE")].value}')
if [[ "$watch_namespace" != media ]]; then
    printf 'ERROR: expected controller to watch media; got %s\n' "$watch_namespace" >&2
    failed=1
fi
kubectl get clusterrole,clusterrolebinding --output yaml >"$work_dir/rbac.yaml"
if [[ -n "${TEST_ARTIFACTS_DIR:-}" ]]; then
    mkdir -p -- "$TEST_ARTIFACTS_DIR"
    cp -- "$work_dir/rbac.yaml" "$TEST_ARTIFACTS_DIR/rbac.yaml"
fi
((failed == 0))
printf 'Native Jellyfin provider installed under restricted PSA; APIs are namespaced, controller watches media, and Crossplane supplies the provider and composition permissions.\n'
python3 "$repo_dir/packages/provider-jellyfin/tests/verify_kind_reconciliation.py"
