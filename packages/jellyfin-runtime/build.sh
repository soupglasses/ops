#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT
set -euo pipefail
package_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
image=${1:-ops-jellyfin:development}
work_dir=$(mktemp -d)
trap 'rm -rf -- "$work_dir"' EXIT
shopt -s nullglob
lock=$package_dir/plugins.lock.json
for name in $(jq -r 'keys[]' "$lock"); do
    repository=$(jq -r --arg name "$name" '.[$name].repository' "$lock")
    revision=$(jq -r --arg name "$name" '.[$name].licenseRevision' "$lock")
    tag=$(jq -r --arg name "$name" '.[$name].tag' "$lock")
    sha256=$(jq -r --arg name "$name" '.[$name].sha256' "$lock")
    mkdir -p "$work_dir/archives/$name" "$work_dir/context/plugins" "$work_dir/context/licenses/$name"
    gh release download "$tag" --repo "$repository" --pattern '*.zip' --dir "$work_dir/archives/$name"
    archives=("$work_dir/archives/$name"/*.zip)
    [[ ${#archives[@]} == 1 ]] || { echo "Expected exactly one $name plugin archive" >&2; exit 1; }
    archive=${archives[0]}
    printf '%s  %s\n' "$sha256" "$archive" | sha256sum --check --status
    version=$(unzip -p "$archive" meta.json | jq -er '.version | select(test("^[0-9]+(\\.[0-9]+){1,3}$"))')
    unzip -q "$archive" -d "$work_dir/context/plugins/${name}_$version"
    gh api "repos/$repository/contents/LICENSE?ref=$revision" --jq .content \
        | base64 --decode > "$work_dir/context/licenses/$name/LICENSE"
done
docker build --build-context "plugins=$work_dir/context" --tag "$image" "$package_dir"
