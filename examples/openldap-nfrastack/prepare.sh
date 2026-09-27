#!/bin/sh
# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
#
# SPDX-License-Identifier: 0BSD

# Run once as UID/GID 389 with /data/db also mounted at /var/lib/openldap.
# Mount /source/{prepare.conf,oauth.schema.conf}, /certs, /secrets/admin,
# and the old native export at /restore/directory.ldif read-only.
# The source export and original PVC are never modified. Failed preparation
# leaves the new target for inspection; use a fresh target for another attempt.
set -eu
umask 077

fail() {
    echo "$1" >&2
    exit 1
}

[ "$(id -u)" = 389 ] || fail 'Preparation must run as UID 389.'
[ "$(id -g)" = 389 ] || fail 'Preparation must run as GID 389.'
test -s /secrets/admin || fail 'Mounted administrator credential is missing.'
test -s /restore/directory.ldif || fail 'Source directory export is missing.'
test -s /source/prepare.conf || fail 'Preparation configuration is missing.'
test -s /source/oauth.schema.conf || fail 'OAuth schema is missing.'
test -s /certs/tls.crt || fail 'TLS certificate is missing.'
test -s /certs/tls.key || fail 'TLS private key is missing.'
mkdir -p /data/config /data/db /run/openldap/run
for directory in /data/config /data/db /var/lib/openldap; do
    [ -z "$(find "$directory" -mindepth 1 -maxdepth 1 -print -quit)" ] ||
        fail 'Target configuration/database must be empty; refusing to overwrite data.'
done

# Confirm that import and runtime resolve the same database directory.
test /data/db -ef /var/lib/openldap || fail 'Mount /data/db at /var/lib/openldap.'
work=$(mktemp -d /data/.prepare.XXXXXX)
trap 'rm -rf "$work"' EXIT
trap 'exit 1' HUP INT TERM

# slappasswd reads the Secret directly. Neither plaintext nor its hash appears
# in command arguments or logs; temporary files stay on the private target PVC.
password_hash=$(/usr/sbin/slappasswd -o module-path=/usr/lib/openldap \
    -o module-load=argon2 -h '{ARGON2}' -T /secrets/admin)
while IFS= read -r line || [ -n "$line" ]; do
    if [ "$line" = '@ROOT_PASSWORD_HASH@' ]; then
        printf 'rootpw %s\n' "$password_hash"
    else
        printf '%s\n' "$line"
    fi
done < /source/prepare.conf > "$work/slapd.conf"
unset password_hash

# Suppress parser/import diagnostics: malformed input can quote sensitive LDIF.
# On failure keep private diagnostics on the target, never stream them to logs.
if ! /usr/sbin/slaptest -f "$work/slapd.conf" -F /data/config -n 0 \
    > /data/prepare-config.log 2>&1; then
    fail 'Configuration conversion failed; private diagnostics remain on the target PVC.'
fi
if ! /usr/sbin/slapadd -F /data/config -n 1 -l /restore/directory.ldif \
    > /data/prepare-import.log 2>&1; then
    fail 'Directory import failed; private diagnostics remain on the target PVC.'
fi
test -s /data/db/data.mdb || fail 'Import produced no database.'
rm /data/prepare-config.log /data/prepare-import.log
echo 'Directory imported; validate authentication, identities, TLS, and restore before cutover.'
