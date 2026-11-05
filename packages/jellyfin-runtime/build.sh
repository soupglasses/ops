#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT
set -euo pipefail
package_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
image=${1:-ops-jellyfin:development}
work_dir=$(mktemp -d)
trap 'rm -rf -- "$work_dir"' EXIT
ldap_repository=$(jq -r .ldap.repository "$package_dir/plugins.lock.json")
ldap_revision=$(jq -r .ldap.licenseRevision "$package_dir/plugins.lock.json")
ldap_tag=$(jq -r .ldap.tag "$package_dir/plugins.lock.json")
ldap_sha256=$(jq -r .ldap.sha256 "$package_dir/plugins.lock.json")
gh release download "$ldap_tag" --repo "$ldap_repository" --pattern 'ldap-authentication_*.zip' --dir "$work_dir"
shopt -s nullglob
archives=("$work_dir"/*.zip)
[[ ${#archives[@]} == 1 ]] || { echo 'Expected exactly one LDAP plugin archive' >&2; exit 1; }
ldap_archive=${archives[0]}
printf '%s  %s\n' "$ldap_sha256" "$ldap_archive" | sha256sum --check --status
ldap_version=$(unzip -p "$ldap_archive" meta.json | jq -er \
    'select(.guid == "958aad66-3784-4d2a-b89a-a7b6fab6e25c") | .version | select(test("^[0-9]+(\\.[0-9]+){1,3}$"))')
mkdir "$work_dir/ldap" "$work_dir/license"
unzip -q "$ldap_archive" -d "$work_dir/ldap"
gh api "repos/$ldap_repository/contents/LICENSE?ref=$ldap_revision" --jq .content \
    | base64 --decode > "$work_dir/license/LICENSE"
docker build \
    --build-arg "LDAP_PLUGIN_VERSION=$ldap_version" \
    --build-context "ldap-plugin=$work_dir/ldap" \
    --build-context "ldap-license=$work_dir/license" \
    --tag "$image" "$package_dir"
