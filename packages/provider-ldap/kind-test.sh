#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT
set -euo pipefail
repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
cluster="ops-ldap-test-$$"
work_dir=$(mktemp -d)
export KUBECONFIG="$work_dir/kubeconfig"
provider_image=${PROVIDER_IMAGE:-ops-provider-ldap:development}
tests_image=${TESTS_IMAGE:-ops-provider-ldap-tests:development}
trap 'kind delete cluster --name "$cluster"; rm -rf -- "$work_dir"' EXIT
kind create cluster --name "$cluster" --kubeconfig "$KUBECONFIG" \
    --image kindest/node:v1.34.11@sha256:44e222ee2132dab25ff87301682f89eb82c7880ea3a1bf543bfe9708fd08d67d
kind load docker-image --name "$cluster" "$provider_image" "$tests_image"
# Match the production namespace contract so missing runtime security settings fail admission.
for namespace in crossplane-system ldap-test; do
    kubectl create namespace "$namespace"
    kubectl label namespace "$namespace" \
        pod-security.kubernetes.io/enforce=restricted \
        pod-security.kubernetes.io/enforce-version=v1.34 \
        pod-security.kubernetes.io/audit=restricted \
        pod-security.kubernetes.io/audit-version=v1.34 \
        pod-security.kubernetes.io/warn=restricted \
        pod-security.kubernetes.io/warn-version=v1.34
done
if kubectl run psa-rejection --namespace ldap-test --restart=Never --dry-run=server \
    --image="$tests_image" --command -- /bin/true >"$work_dir/psa-rejection.log" 2>&1; then
    echo 'ERROR: restricted Pod Security did not reject an insecure test Pod' >&2
    exit 1
fi
grep -q 'violates PodSecurity "restricted:v1.34"' "$work_dir/psa-rejection.log"
helm upgrade --install crossplane crossplane --repo https://charts.crossplane.io/stable \
    --version 2.4.2 --namespace crossplane-system \
    --set-json 'securityContextCrossplane={"runAsNonRoot":true,"allowPrivilegeEscalation":false,"capabilities":{"drop":["ALL"]},"seccompProfile":{"type":"RuntimeDefault"}}' \
    --set-json 'securityContextRBACManager={"runAsNonRoot":true,"allowPrivilegeEscalation":false,"capabilities":{"drop":["ALL"]},"seccompProfile":{"type":"RuntimeDefault"}}' \
    --set 'args[0]=--poll-interval=2s' --wait --timeout 4m
helm upgrade --install ldap "$repo_dir/charts/ldap-provider" \
    --namespace ldap-test \
    --set image.repository="${provider_image%:*}" --set image.tag="${provider_image##*:}" \
    --set tests.image.repository="${tests_image%:*}" --set tests.image.tag="${tests_image##*:}" \
    --set pollInterval=2s --set tests.enabled=true --set tests.mode=integration \
    --set oauth2.enabled=true --set oauth2.clientsDN=ou=clients\\,ou=oauth\\,dc=example\\,dc=test \
    --wait --timeout 3m
kubectl wait function/function-go-templating --for=condition=Healthy --timeout=180s
kubectl wait crd/oauth2clients.identity.ops.finnes.dev --for=condition=Established --timeout=90s
helm test ldap --namespace ldap-test --logs --timeout 9m
kubectl delete pod ldap-tests --namespace ldap-test
helm upgrade ldap "$repo_dir/charts/ldap-provider" --namespace ldap-test \
    --reuse-values --set tests.mode=smoke --wait
helm test ldap --namespace ldap-test --logs --timeout 2m
kubectl delete pod ldap-tests --namespace ldap-test
