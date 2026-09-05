#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Soup Bot <ops+bot@finnes.dev>
#
# SPDX-License-Identifier: 0BSD

set -euo pipefail

repo_root=$(git rev-parse --show-toplevel)
zero=0000000000000000000000000000000000000000
failed=0

while read -r local_ref local_sha remote_ref remote_sha; do
    # Deleting a remote ref introduces no commits.
    [[ "$local_sha" == "$zero" ]] && continue

    if [[ "$remote_sha" == "$zero" ]]; then
        revision="$local_sha"
        exclusions=()
        while read -r existing_ref; do
            exclusions+=("^$existing_ref")
        done < <(git for-each-ref --format='%(objectname)' refs/remotes/)
    else
        revision="$remote_sha..$local_sha"
        exclusions=()
    fi

    while read -r commit; do
        [[ -z "$commit" ]] && continue
        if ! git -c gpg.ssh.allowedSignersFile="$repo_root/.allowed_signers" \
            verify-commit "$commit" >/dev/null 2>&1; then
            printf 'ERROR: refusing to push unsigned or untrusted commit %s to %s\n' \
                "$commit" "$remote_ref" >&2
            failed=1
        fi
    done < <(git rev-list "$revision" "${exclusions[@]}")
done

exit "$failed"
