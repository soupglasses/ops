#!/bin/sh
# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
#
# SPDX-License-Identifier: 0BSD

# LMDB supplies a transaction-consistent directory export. Configuration changes
# need their own maintenance window; do not race schema/ACL migrations with backup.
set -eu
umask 077
mkdir -p /data/backup
# Serialize scheduled and operator-triggered exports without stale lock files.
exec 9>/data/backup/.lock
flock -n 9
next=$(mktemp -d /data/backup/generation.XXXXXX)
cleanup() {
    # A signal after publication must not remove the new current generation.
    [ "$(readlink /data/backup/current || true)" = "$(basename "$next")" ] || rm -rf "$next"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
slapcat -F /data/config -n 0 -l "$next/config.ldif"
slapcat -F /data/config -n 1 -l "$next/directory.ldif"
test -s "$next/config.ldif"
test -s "$next/directory.ldif"
# Publish the pair atomically; failed exports cannot become a successful backup.
ln -s "$(basename "$next")" "$next/publish"
mv -Tf "$next/publish" /data/backup/current
trap - EXIT HUP INT TERM
for old in /data/backup/generation.*; do
    [ "$old" = "$next" ] || rm -rf "$old"
done
echo 'Native configuration and directory exports prepared.'
