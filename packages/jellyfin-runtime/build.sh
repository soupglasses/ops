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
sso_repository=$(jq -r .sso.repository "$package_dir/plugins.lock.json")
sso_tag=$(jq -r .sso.tag "$package_dir/plugins.lock.json")
sso_asset=$(jq -r .sso.asset "$package_dir/plugins.lock.json")
sso_sha256=$(jq -r .sso.sha256 "$package_dir/plugins.lock.json")
gh repo clone "$ldap_repository" "$work_dir/ldap" -- --filter=blob:none
git -C "$work_dir/ldap" checkout --detach "$ldap_revision"
rm -rf -- "$work_dir/ldap/.git"
gh repo clone "$sso_repository" "$work_dir/sso-source" -- --depth=1 --branch "$sso_tag"
gh release download "$sso_tag" --repo "$sso_repository" --pattern "$sso_asset" --dir "$work_dir"
printf '%s  %s\n' "$sso_sha256" "$work_dir/$sso_asset" | sha256sum --check --status
mkdir "$work_dir/sso"
unzip -q "$work_dir/$sso_asset" -d "$work_dir/sso"
docker build \
    --build-context "ldap-source=$work_dir/ldap" \
    --build-context "sso-plugin=$work_dir/sso" \
    --build-context "sso-license=$work_dir/sso-source" \
    --tag "$image" "$package_dir"
