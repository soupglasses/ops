#!/bin/sh
# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT
set -eu

data_dir=${LDAP_DATA_DIR:-/data}
# Keep these descriptors inherited by foreground slapd, including during shutdown.
exec 9>"$data_dir/.slapd.lock"
flock 9
exec 8>"$data_dir/.configuration.lock"
flock -s 8
exec /usr/sbin/slapd "$@"
