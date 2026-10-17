#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT
set -euo pipefail
package_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
image=${1:-ops-jellyfin:development}
work_dir=$(mktemp -d)
trap 'rm -rf -- "$work_dir"' EXIT
ldap_repository=$(jq -r .ldap.repository "$package_dir/plugins.lock.json")
ldap_revision=$(jq -r .ldap.revision "$package_dir/plugins.lock.json")
ldap_tag=$(jq -r .ldap.tag "$package_dir/plugins.lock.json")
ldap_asset=$(jq -r .ldap.asset "$package_dir/plugins.lock.json")
ldap_sha256=$(jq -r .ldap.sha256 "$package_dir/plugins.lock.json")
gh release download "$ldap_tag" --repo "$ldap_repository" --pattern "$ldap_asset" --dir "$work_dir"
printf '%s  %s\n' "$ldap_sha256" "$work_dir/$ldap_asset" | sha256sum --check --status
mkdir "$work_dir/ldap" "$work_dir/license"
unzip -q "$work_dir/$ldap_asset" -d "$work_dir/ldap"
gh api "repos/$ldap_repository/contents/LICENSE?ref=$ldap_revision" --jq .content \
    | base64 --decode > "$work_dir/license/LICENSE"
docker build \
    --build-context "ldap-plugin=$work_dir/ldap" \
    --build-context "ldap-license=$work_dir/license" \
    --tag "$image" "$package_dir"
