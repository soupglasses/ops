#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT
set -euo pipefail
cd "$(dirname "$0")"
go run -buildvcs=false ./cmd/generate
controller-gen object:headerFile=hack/boilerplate.go.txt paths=./apis/...
angryjet generate-methodsets --header-file=hack/boilerplate.go.txt ./apis/...
controller-gen crd:allowDangerousTypes=true paths=./apis/namespaced/... output:crd:dir=generated/crds
for manifest in generated/crds/*.yaml; do
    yamlfmt "$manifest"
    cat > "$manifest.license" <<'LICENSE'
SPDX-FileCopyrightText: 2024 The Crossplane Authors <https://crossplane.io>
SPDX-License-Identifier: Apache-2.0
LICENSE
done
