#!/bin/sh
# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
#
# SPDX-License-Identifier: 0BSD

set -eu

# A projected Secret updates files, but slapd keeps an in-memory TLS context.
# Replacing both paths in one LDAP operation reloads the matching key and chain.
# Local SASL EXTERNAL authenticates UID/GID 389 without a config password. Its
# cn=config ACL permits only these two TLS attributes, not other server settings.
reload() {
    ldapmodify -Q -Y EXTERNAL -H ldapi:/// <<'EOF'
dn: cn=config
changetype: modify
replace: olcTLSCertificateFile
olcTLSCertificateFile: /certs/tls.crt
-
replace: olcTLSCertificateKeyFile
olcTLSCertificateKeyFile: /certs/tls.key
EOF
}

if [ "$#" -gt 0 ] && [ "$1" = "--once" ]; then
    reload
    exit
fi

previous=""
while :; do
    current=$(sha256sum /certs/tls.crt | cut -d ' ' -f 1)
    if [ "$current" != "$previous" ]; then
        if reload; then
            previous=$current
        else
            echo 'TLS reload failed; retrying without acknowledging the new certificate' >&2
        fi
    fi
    sleep 30
done
