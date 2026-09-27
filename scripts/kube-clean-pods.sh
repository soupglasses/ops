#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
#
# SPDX-License-Identifier: 0BSD

set -euo pipefail

# Pending pods may be waiting for VolSync or storage during recovery.
for phase in Failed Succeeded; do
    kubectl delete pods --all-namespaces --field-selector "status.phase=${phase}" \
        --ignore-not-found=true --wait=false
done
