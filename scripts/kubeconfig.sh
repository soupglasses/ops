#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
#
# SPDX-License-Identifier: MIT

set -euo pipefail

if (( $# > 1 )); then
    echo "Usage: $0 [output-file]" >&2
    exit 2
fi

output="${1:-.secure/kubeconfig}"
umask 077
mkdir -p "$(dirname "$output")"
temporary=$(mktemp "${output}.XXXXXX")
trap 'rm -f "$temporary"' EXIT

ssh -n -F /dev/null -o BatchMode=yes -o ConnectTimeout=10 \
    -i "$HOME/.ssh/ansible" -o StrictHostKeyChecking=yes \
    -o HostKeyAlias=nas.home.finnes.dev ansible@172.21.71.10 \
    'sudo -n cat /etc/rancher/rke2/rke2.yaml' > "$temporary"
yq -i '.clusters[].cluster.server = "https://k8s.home.finnes.dev:6443"' "$temporary"

# Keep working credentials until their replacement passes an authenticated API request.
kubectl --kubeconfig "$temporary" --request-timeout=15s get nodes
if [[ -f "$output" ]]; then
    backup=$(mktemp "${output}.backup.XXXXXX")
    cp -- "$output" "$backup"
fi
mv -- "$temporary" "$output"
printf 'Installed %s\n' "$output"
