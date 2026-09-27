#!/bin/sh
# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
#
# SPDX-License-Identifier: 0BSD

# Run only against the isolated candidate, with the existing root credential
# mounted at /secrets/admin. A disposable reader never changes existing accounts.
set -eu
umask 077
export LDAPTLS_REQCERT=demand
uri=ldaps://ldap.finnes.dev:1636
suffix=dc=finnes,dc=dev
admin=cn=admin,$suffix
probe=uid=migration-probe,ou=system,$suffix
group=cn=ldap-readers,ou=groups,$suffix
work=$(mktemp -d /run/openldap/verify.XXXXXX)
created=false
rootldap() {
    tool=$1
    shift
    "$tool" -x -H "$uri" -D "$admin" -y /secrets/admin "$@"
}
cleanup() {
    if [ "$created" = true ]; then
        rootldap ldapdelete "$probe" >/dev/null || echo 'ERROR: remove migration-probe before cutover' >&2
    fi
    rm -rf "$work"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
rootldap ldapwhoami >/dev/null
if rootldap ldapsearch -LLL -b "$probe" -s base dn >"$work/existing" 2>/dev/null; then
    echo 'Probe DN already exists; refusing to replace it.' >&2
    exit 1
fi
# ldap -y reads the whole file; keep its bytes identical to the LDIF value.
openssl rand -hex 32 | tr -d '\n' > "$work/password"
{
    printf 'dn: %s\nobjectClass: inetOrgPerson\nuid: migration-probe\ncn: Migration probe\nsn: Probe\n' "$probe"
    printf 'pwdPolicySubentry: cn=system,ou=policies,%s\nuserPassword: %s\n' "$suffix" "$(cat "$work/password")"
} | rootldap ldapadd >/dev/null
created=true
printf 'dn: %s\nchangetype: modify\nadd: member\nmember: %s\n' "$group" "$probe" | rootldap ldapmodify >/dev/null
reader() {
    tool=$1
    shift
    "$tool" -x -H "$uri" -D "$probe" -y "$work/password" "$@"
}
reader ldapwhoami >/dev/null
reader ldapsearch -LLL -b "$suffix" '(objectClass=inetOrgPerson)' uid entryUUID > "$work/users"
grep -q '^uid:' "$work/users"
grep -q '^entryUUID:' "$work/users"
reader ldapsearch -LLL -b "ou=users,$suffix" '(objectClass=*)' '*' '+' > "$work/attributes"
if grep -Eiq '^(userPassword|oathSecret|pwdHistory):' "$work/attributes"; then
    echo 'Reader can see a protected attribute' >&2; exit 1
fi
reader ldapsearch -LLL -b "ou=oauth,$suffix" '(objectClass=*)' '*' > "$work/oauth" 2>/dev/null || true
if grep -q '^dn:' "$work/oauth"; then echo 'Reader can see OAuth entries' >&2; exit 1; fi
status=0
printf 'dn: %s\nchangetype: modify\nreplace: cn\ncn: Forbidden\n' "$probe" |
    reader ldapmodify >"$work/modify" 2>&1 || status=$?
[ "$status" = 50 ] || { echo 'Reader write was not rejected'; exit 1; }
for protocol in tls1_2 tls1_3; do
    openssl s_client -connect 127.0.0.1:1636 -servername ldap.finnes.dev \
        -verify_hostname ldap.finnes.dev -verify_return_error -"$protocol" </dev/null >"$work/tls" 2>&1
done
for protocol in tls1 tls1_1; do
    if openssl s_client -connect 127.0.0.1:1636 -"$protocol" -cipher 'DEFAULT:@SECLEVEL=0' \
        </dev/null >"$work/tls-old" 2>&1; then
        echo 'Obsolete TLS accepted'; exit 1
    fi
done
if ldapwhoami -x -H ldap://127.0.0.1:389 -o nettimeout=3 >"$work/plain" 2>&1; then
    echo 'Plain LDAP listener accepted a connection'; exit 1
fi
rootldap ldapdelete "$probe" >/dev/null
created=false
rootldap ldapsearch -LLL -b "$group" -s base member > "$work/group"
if grep -q 'migration-probe' "$work/group"; then echo 'Probe group reference remained'; exit 1; fi
echo 'PASS: real admin bind, disposable reader bind/search/denials, TLS 1.2/1.3, old TLS rejection, port 389 closed'
