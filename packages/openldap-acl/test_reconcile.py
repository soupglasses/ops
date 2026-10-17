# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT

import base64
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

import reconcile as acl

BASE = "dc=example,dc=test"
DATABASE = "olcDatabase={1}mdb,cn=config"
SERVICE = "uid=media.jellyfin,ou=system," + BASE
ROOT = "gidNumber=0+uidNumber=0,cn=peercred,cn=external,cn=auth"
RULE = f'to dn.subtree="ou=users,{BASE}" filter="(memberOf=cn=media.jellyfin-users,ou=groups,{BASE})" attrs=entry,uid,cn,displayName,givenName,sn,mail,memberOf,jpegPhoto by dn.exact="{SERVICE}" read by * break'
PASSWORD_RULE = "to attrs=userPassword by self write by anonymous auth by * none"
LEGACY = ["{0}" + PASSWORD_RULE, "{1}to * by self read by * none"]
URI = "ldapi://%2Ffixture%2Frun%2Fldapi/"


class Plans(unittest.TestCase):
    def test_preserves_human_rules_and_password_first(self):
        human = "{1}to attrs=description by self write by * break"
        observed = [LEGACY[0], human, LEGACY[1].replace("{1}", "{2}")]
        deleted, added = acl.plan(observed, [RULE], SERVICE)
        self.assertEqual(deleted, [])
        self.assertEqual(added, ["{1}" + RULE])
        self.assertEqual(observed[0], LEGACY[0])

    def test_drift_repair_deletes_only_owned_rule(self):
        broken = "{1}" + RULE.replace(
            "attrs=entry,uid,cn,displayName,givenName,sn,mail,memberOf,jpegPhoto",
            "attrs=jpegPhoto",
        )
        observed = [LEGACY[0], broken, LEGACY[1].replace("{1}", "{2}")]
        deleted, added = acl.plan(observed, [RULE], SERVICE)
        self.assertEqual(deleted, [broken])
        self.assertEqual(added, ["{1}" + RULE])

    def test_canonical_server_output_is_idempotent(self):
        existing = [
            LEGACY[0],
            "{1}" + RULE.replace("dn.exact=", "dn.base="),
            LEGACY[1].replace("{1}", "{2}"),
        ]
        self.assertEqual(acl.plan(existing, [RULE], SERVICE), ([], []))

    def test_ambiguous_ownership_and_missing_anchor_fail_closed(self):
        with self.assertRaises(acl.ReconcileError):
            acl.plan([RULE, RULE, LEGACY[1]], [RULE], SERVICE)
        with self.assertRaises(acl.ReconcileError):
            acl.plan([LEGACY[1]], [RULE], SERVICE)

    def test_folded_base64_ldif(self):
        encoded = base64.b64encode(RULE.encode()).decode()
        data = (
            f"dn: {DATABASE}\nolcAccess:: {encoded[:40]}\n {encoded[40:]}\n\n".encode()
        )
        self.assertEqual(acl.parse_ldif(data)[0]["olcaccess"], [RULE])

    def test_policy_rejects_password_and_wildcard_grants(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "policy.ldif"
            for attributes in ["userPassword", "*", "oauthClientSecret"]:
                path.write_text(
                    f"dn: {DATABASE}\nolcAccess: "
                    + RULE.replace(
                        "entry,uid,cn,displayName,givenName,sn,mail,memberOf,jpegPhoto",
                        attributes,
                    )
                    + "\n"
                )
                with self.assertRaises(acl.ReconcileError):
                    acl.policy(path)


def docker(*arguments, data=None, check=True):
    result = subprocess.run(["docker", *arguments], input=data, capture_output=True)
    if check and result.returncode:
        raise AssertionError(
            "fixture command failed: " + result.stderr.decode(errors="replace")[-2000:]
        )
    return result


@unittest.skipUnless(
    os.getenv("OPENLDAP_ACL_TEST_IMAGE"),
    "set OPENLDAP_ACL_TEST_IMAGE for matching-version real LDAP tests",
)
class RealLDAP(unittest.TestCase):
    def setUp(self):
        self.container = (
            docker(
                "run",
                "--detach",
                "--user",
                "0:0",
                "--ulimit",
                "nofile=1024:1024",
                "--entrypoint",
                "/bin/sh",
                os.environ["OPENLDAP_ACL_TEST_IMAGE"],
                "-c",
                "sleep infinity",
            )
            .stdout.decode()
            .strip()
        )
        self.addCleanup(lambda: docker("rm", "--force", self.container))
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.policy = Path(self.temporary.name) / "policy.ldif"
        self.policy.write_text(f"dn: {DATABASE}\nolcAccess: {RULE}\n")
        configuration = f"""include /etc/openldap/schema/core.schema
include /etc/openldap/schema/cosine.schema
include /etc/openldap/schema/inetorgperson.schema
TLSCertificateFile /fixture/tls.crt
TLSCertificateKeyFile /fixture/tls.key
modulepath /usr/lib/openldap
moduleload back_mdb
moduleload memberof
pidfile /fixture/run/slapd.pid
argsfile /fixture/run/slapd.args
database config
access to attrs=olcTLSCertificateFile,olcTLSCertificateKeyFile by dn.exact="gidNumber=389+uidNumber=389,cn=peercred,cn=external,cn=auth" write by * break
access to * by dn.exact="{ROOT}" manage by * none
database mdb
maxsize 10485760
suffix "{BASE}"
rootdn "cn=admin,{BASE}"
rootpw disposable-fixture-only
directory /fixture/db
access to attrs=userPassword by self write by anonymous auth by * none
access to attrs=description by self write by * break
access to attrs=entry,uid,cn,sn,mail,memberOf by dn.exact="{SERVICE}" read by self read by * break
access to * by self read by * none
overlay memberof
"""
        self.exec(
            [
                "/bin/sh",
                "-ec",
                "mkdir -p /fixture/config /fixture/db /fixture/run; chown -R 389:389 /fixture",
            ]
        )
        self.exec(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-subj",
                "/CN=fixture",
                "-keyout",
                "/fixture/tls.key",
                "-out",
                "/fixture/tls.crt",
                "-days",
                "1",
            ],
            user="389:389",
        )
        self.exec(
            ["/bin/sh", "-ec", "cat > /fixture/slapd.conf"], data=configuration.encode()
        )
        self.exec(
            ["/bin/sh", "-ec", "cat > /fixture/policy.ldif"],
            data=self.policy.read_bytes(),
        )
        self.exec(
            ["slapadd", "-f", "/fixture/slapd.conf", "-b", BASE],
            user="389:389",
            data=f"dn: {BASE}\nobjectClass: domain\ndc: example\n\n".encode(),
        )
        self.exec(
            ["slaptest", "-f", "/fixture/slapd.conf", "-F", "/fixture/config"],
            user="389:389",
        )

    def exec(self, arguments, user="0:0", data=None, check=True):
        return docker(
            "exec",
            "--interactive",
            "--user",
            user,
            self.container,
            *arguments,
            data=data,
            check=check,
        )

    def bootstrap(self, check=True, timeout=2):
        return self.exec(
            [
                "python3",
                "/usr/local/lib/openldap-acl/reconcile.py",
                "--policy",
                "/fixture/policy.ldif",
                "--config-directory",
                "/fixture/config",
                "--lock-timeout",
                str(timeout),
            ],
            user="389:389",
            check=check,
        )

    def start(self):
        docker(
            "exec",
            "--detach",
            "--user",
            "389:389",
            "--env",
            "LDAP_DATA_DIR=/fixture",
            self.container,
            "/bin/sh",
            "/usr/local/lib/openldap-acl/serve.sh",
            "-F",
            "/fixture/config",
            "-h",
            "ldap://127.0.0.1:1389/ " + URI,
            "-d",
            "256",
        )
        for _ in range(100):
            result = self.exec(
                ["ldapwhoami", "-Q", "-Y", "EXTERNAL", "-H", URI], check=False
            )
            if result.returncode == 0:
                return
            time.sleep(0.1)
        self.fail("matching-version LDAP fixture did not start")

    def search(self, base, attributes):
        raw = self.exec(
            [
                "ldapsearch",
                "-LLL",
                "-o",
                "ldif-wrap=no",
                "-x",
                "-H",
                "ldap://127.0.0.1:1389",
                "-D",
                SERVICE,
                "-w",
                "disposable-fixture-only",
                "-b",
                base,
                "-s",
                "base",
                "(uid=*)",
                *attributes,
            ]
        ).stdout
        lines = raw.splitlines()
        entries = acl.parse_ldif(
            b"\n".join(
                line for line in lines if not line.lower().startswith(b"jpegphoto:")
            )
        )
        for line in lines:
            if line.lower().startswith(b"jpegphoto::"):
                entries[0]["jpegphoto"] = [base64.b64decode(line.split(b"::", 1)[1])]
        return entries

    def test_offline_init_waits_for_a_backup_reader_without_a_daemon(self):
        docker(
            "exec",
            "--detach",
            "--user",
            "389:389",
            self.container,
            "/bin/sh",
            "-ec",
            'exec 8>/fixture/.configuration.lock; flock -s 8; echo "$$" >/fixture/backup-reader.pid; exec sleep 30',
        )
        for _ in range(100):
            ready = self.exec(["test", "-s", "/fixture/backup-reader.pid"], check=False)
            if ready.returncode == 0:
                break
            time.sleep(0.02)
        self.assertEqual(ready.returncode, 0)
        self.assertNotEqual(self.bootstrap(check=False, timeout=0.2).returncode, 0)
        self.exec(["/bin/sh", "-ec", 'kill "$(cat /fixture/backup-reader.pid)"'])
        self.bootstrap()

    def test_offline_tools_do_not_require_mounted_tls_keys(self):
        self.exec(
            [
                "/bin/sh",
                "-ec",
                "mv /fixture/tls.key /fixture/tls.key.hidden; mv /fixture/tls.crt /fixture/tls.crt.hidden",
            ],
            user="389:389",
        )
        self.bootstrap()

    def test_native_rule_update_preserves_human_rules(self):
        self.bootstrap()
        before = acl.parse_ldif(
            self.exec(
                ["slapcat", "-F", "/fixture/config", "-n", "0"], user="389:389"
            ).stdout
        )
        original = next(item for item in before if item.get("dn") == [DATABASE])[
            "olcaccess"
        ]
        narrowed = RULE.replace(
            "entry,uid,cn,displayName,givenName,sn,mail,memberOf,jpegPhoto", "jpegPhoto"
        )
        self.exec(
            ["/bin/sh", "-ec", "cat > /fixture/policy.ldif"],
            data=f"dn: {DATABASE}\nolcAccess: {narrowed}\n".encode(),
        )
        self.bootstrap()
        changed = acl.parse_ldif(
            self.exec(
                ["slapcat", "-F", "/fixture/config", "-n", "0"], user="389:389"
            ).stdout
        )
        current = next(item for item in changed if item.get("dn") == [DATABASE])[
            "olcaccess"
        ]
        self.assertEqual(
            [
                acl.normalized(value)
                for value in original
                if not acl.belongs(value, SERVICE)
            ],
            [
                acl.normalized(value)
                for value in current
                if not acl.belongs(value, SERVICE)
            ],
        )
        self.assertEqual(acl.normalized(current[1]), acl.normalized(narrowed))
        self.exec(
            ["/bin/sh", "-ec", "cat > /fixture/policy.ldif"],
            data=self.policy.read_bytes(),
        )
        self.bootstrap()

    def test_old_backup_restore_bootstrap_preserves_legacy_and_denies_secrets(self):
        old = self.exec(
            ["slapcat", "-F", "/fixture/config", "-n", "0"], user="389:389"
        ).stdout
        old_entries = acl.parse_ldif(old)
        previous = next(item for item in old_entries if item.get("dn") == [DATABASE])[
            "olcaccess"
        ]
        self.bootstrap()
        self.bootstrap()
        # Restore an older native backup with no managed grant, then recreate it.
        self.exec(
            [
                "/bin/sh",
                "-ec",
                "rm -rf /fixture/config; mkdir /fixture/config; chown 389:389 /fixture/config",
            ]
        )
        self.exec(
            ["slapadd", "-F", "/fixture/config", "-n", "0"], user="389:389", data=old
        )
        self.bootstrap()
        checked = acl.parse_ldif(
            self.exec(
                ["slapcat", "-F", "/fixture/config", "-n", "0"], user="389:389"
            ).stdout
        )
        entry = next(item for item in checked if item.get("dn") == [DATABASE])
        rules = entry["olcaccess"]
        self.assertEqual(
            [
                acl.normalized(value)
                for value in rules
                if not acl.belongs(value, SERVICE)
            ],
            [
                acl.normalized(value)
                for value in previous
                if not acl.belongs(value, SERVICE)
            ],
        )
        self.assertEqual(acl.normalized(rules[0]), acl.normalized(previous[0]))
        self.assertEqual(acl.normalized(rules[1]), acl.normalized(RULE))
        old_config = next(
            item
            for item in old_entries
            if item.get("dn") == ["olcDatabase={0}config,cn=config"]
        )
        new_config = next(
            item for item in checked if item.get("dn") == old_config["dn"]
        )
        self.assertEqual(old_config["olcaccess"], new_config["olcaccess"])
        self.start()
        # Actual slapd must retain both inherited locks after shell exec.
        self.assertNotEqual(
            self.exec(
                ["flock", "-xn", "/fixture/.slapd.lock", "true"],
                user="389:389",
                check=False,
            ).returncode,
            0,
        )
        self.assertNotEqual(
            self.exec(
                ["flock", "-xn", "/fixture/.configuration.lock", "true"],
                user="389:389",
                check=False,
            ).returncode,
            0,
        )
        self.assertNotEqual(self.bootstrap(check=False, timeout=0.3).returncode, 0)
        # A native export can share the configuration lock with a live daemon.
        self.exec(
            [
                "/bin/sh",
                "-ec",
                "exec 8>/fixture/.configuration.lock; flock -sn 8; slapcat -F /fixture/config -n 0 >/fixture/config-backup.ldif; slapcat -F /fixture/config -n 1 >/fixture/directory-backup.ldif; test -s /fixture/config-backup.ldif; test -s /fixture/directory-backup.ldif",
            ],
            user="389:389",
        )
        jpeg = (
            Path(__file__).parents[1] / "jellyfin-runtime/tests/profile.jpg"
        ).read_bytes()
        records = f"""dn: ou=users,{BASE}
objectClass: organizationalUnit
ou: users

dn: ou=groups,{BASE}
objectClass: organizationalUnit
ou: groups

dn: ou=system,{BASE}
objectClass: organizationalUnit
ou: system

dn: {SERVICE}
objectClass: account
objectClass: simpleSecurityObject
uid: media.jellyfin
userPassword: disposable-fixture-only

dn: uid=alice,ou=users,{BASE}
objectClass: inetOrgPerson
uid: alice
cn: Alice
sn: Fixture
userPassword: synthetic-user-password
jpegPhoto:: {base64.b64encode(jpeg).decode()}

dn: uid=bob,ou=users,{BASE}
objectClass: inetOrgPerson
uid: bob
cn: Bob
sn: Fixture
userPassword: synthetic-user-password
jpegPhoto:: {base64.b64encode(jpeg).decode()}

dn: cn=media.jellyfin-users,ou=groups,{BASE}
objectClass: groupOfNames
cn: media.jellyfin-users
member: uid=alice,ou=users,{BASE}

"""
        self.exec(
            [
                "ldapadd",
                "-x",
                "-H",
                "ldap://127.0.0.1:1389",
                "-D",
                "cn=admin," + BASE,
                "-w",
                "disposable-fixture-only",
            ],
            data=records.encode(),
        )
        alice = self.search("uid=alice,ou=users," + BASE, ["jpegPhoto", "userPassword"])
        bob = self.search("uid=bob,ou=users," + BASE, ["jpegPhoto", "userPassword"])
        self.assertEqual(len(alice), 1)
        self.assertEqual(alice[0]["jpegphoto"], [jpeg])
        self.assertNotIn("userpassword", alice[0])
        self.assertEqual(len(bob), 1)
        self.assertNotIn("jpegphoto", bob[0])
        self.assertNotIn("userpassword", bob[0])


if __name__ == "__main__":
    unittest.main()
