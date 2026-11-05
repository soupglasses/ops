#!/bin/sh
# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT
set -eu

# A terminating server or backup may still hold this PVC on the same node.
exec 9>/data/.slapd.lock
flock 9
exec 8>/data/.configuration.lock
flock 8
if ! slapmodify -F /data/config -n 0 -l /source/access-policy.ldif >/dev/null 2>&1; then
    echo 'Native OpenLDAP access policy update failed; refusing startup' >&2
    exit 1
fi
echo 'OpenLDAP access policy applied'
