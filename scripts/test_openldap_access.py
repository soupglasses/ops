# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT

"""Opt-in native LDAP tests using production scripts and disposable Docker volumes."""

import base64
import os
from pathlib import Path
import subprocess
import time
import unittest
import uuid

SOURCE = Path(__file__).resolve().parents[1] / "kubernetes/apps/identity/openldap/app"
BASE = "dc=finnes,dc=dev"
ADMIN = "cn=fixture-admin," + BASE
SERVICE = "uid=media.jellyfin,ou=system," + BASE
GROUP_CN = "media.jellyfin.user"
ADMIN_GROUP_CN = "media.jellyfin.admin"
GROUP = f"cn={GROUP_CN},ou=groups,{BASE}"
ADMIN_GROUP = f"cn={ADMIN_GROUP_CN},ou=groups,{BASE}"
PASSWORD = "disposable-native-ldap-fixture"
URI = "ldap://127.0.0.1:1389"
PHOTO = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwg"
    "JC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIy"
    "MjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAACAAIDASIA"
    "AhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9"
    "AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6"
    "Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ip"
    "qrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEB"
    "AQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdh"
    "cRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpj"
    "ZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK"
    "0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwC7RRRXunin/9k="
)


def docker(*arguments, data=None, check=True, timeout=30):
    result = subprocess.run(
        ["docker", *arguments], input=data, capture_output=True, timeout=timeout
    )
    if check and result.returncode:
        raise AssertionError(result.stderr.decode(errors="replace"))
    return result


def attributes(output, name):
    lines = output.decode().replace("\n ", "").splitlines()
    result = []
    for line in lines:
        key, separator, value = line.partition(":")
        if separator and key.casefold() == name.casefold():
            result.append(
                base64.b64decode(value[1:].strip())
                if value.startswith(":")
                else value.lstrip().encode()
            )
    return result


@unittest.skipUnless(
    os.getenv("OPENLDAP_ACCESS_TEST") == "1", "set OPENLDAP_ACCESS_TEST=1"
)
class NativeAccess(unittest.TestCase):
    def setUp(self):
        import yaml

        deployment = yaml.safe_load((SOURCE / "deployment.yaml").read_text())
        self.image = next(
            container["image"]
            for container in deployment["spec"]["template"]["spec"]["containers"]
            if container["name"] == "openldap"
        )
        self.prefix = "ops-native-ldap-" + uuid.uuid4().hex[:10]
        self.volumes = {}
        for purpose in ("data", "db", "source"):
            name = self.prefix + "-" + purpose
            docker("volume", "create", name)
            self.addCleanup(docker, "volume", "rm", name, check=False)
            self.volumes[purpose] = name

        setup = self.prefix + "-setup"
        self.addCleanup(docker, "rm", "--force", setup, check=False)
        docker(
            "run",
            "--detach",
            "--name",
            setup,
            "--user",
            "0:0",
            *self.mounts(readonly_source=False),
            "--entrypoint",
            "/bin/sh",
            self.image,
            "-ec",
            "mkdir -p /data/config; chown -R 389:389 /data; sleep infinity",
        )
        for filename in ("configure-access.sh", "run-slapd.sh", "access-policy.ldif"):
            docker("cp", str(SOURCE / filename), setup + ":/source/" + filename)
        configuration = f"""include /etc/openldap/schema/core.schema
include /etc/openldap/schema/cosine.schema
include /etc/openldap/schema/inetorgperson.schema
modulepath /usr/lib/openldap
moduleload back_mdb
moduleload memberof
pidfile /run/openldap/run/slapd.pid
argsfile /run/openldap/run/slapd.args
database config
access to * by * none
database mdb
maxsize 10485760
suffix "{BASE}"
rootdn "{ADMIN}"
rootpw {PASSWORD}
directory /var/lib/openldap
access to attrs=userPassword by self write by anonymous auth by * none
access to * by * none
overlay memberof
"""
        docker(
            "exec",
            "--interactive",
            setup,
            "/bin/sh",
            "-ec",
            "cat > /data/slapd.conf",
            data=configuration.encode(),
        )
        docker(
            "exec",
            "--user",
            "389:389",
            "--interactive",
            setup,
            "slapadd",
            "-f",
            "/data/slapd.conf",
            "-b",
            BASE,
            data=f"dn: {BASE}\nobjectClass: domain\ndc: finnes\n\n".encode(),
        )
        docker(
            "exec",
            "--user",
            "389:389",
            setup,
            "slaptest",
            "-f",
            "/data/slapd.conf",
            "-F",
            "/data/config",
        )
        docker("rm", "--force", setup)

        self.container = self.prefix + "-server"
        self.addCleanup(docker, "rm", "--force", self.container, check=False)
        docker(
            "run",
            "--detach",
            "--name",
            self.container,
            *self.restricted(),
            *self.mounts(),
            "--tmpfs",
            "/run/openldap:rw,uid=389,gid=389,mode=0750",
            "--entrypoint",
            "/bin/sh",
            self.image,
            "-ec",
            "mkdir -p /run/openldap/run; sleep infinity",
        )
        self.start_server()
        entries = ""
        for ou in ("users", "groups", "system"):
            entries += (
                f"dn: ou={ou},{BASE}\nobjectClass: organizationalUnit\nou: {ou}\n\n"
            )
        for username, ou in (
            ("alice", "users"),
            ("bob", "users"),
            ("charlie", "users"),
            ("media.jellyfin", "system"),
        ):
            entries += f"""dn: uid={username},ou={ou},{BASE}
objectClass: inetOrgPerson
uid: {username}
cn: {username}
sn: Fixture
userPassword: {PASSWORD}
jpegPhoto:: {base64.b64encode(PHOTO).decode()}

"""
        entries += f"""dn: cn=ldap-readers,ou=groups,{BASE}
objectClass: groupOfNames
cn: ldap-readers
member: uid=bob,ou=users,{BASE}

dn: {GROUP}
objectClass: groupOfNames
cn: {GROUP_CN}
member: uid=alice,ou=users,{BASE}

dn: {ADMIN_GROUP}
objectClass: groupOfNames
cn: {ADMIN_GROUP_CN}
member: uid=charlie,ou=users,{BASE}

"""
        self.exec(
            "ldapadd",
            "-x",
            "-H",
            URI,
            "-D",
            ADMIN,
            "-w",
            PASSWORD,
            data=entries.encode(),
        )

    def mounts(self, alias=True, readonly_source=True):
        pairs = [("data", "/data"), ("db", "/data/db"), ("source", "/source")]
        if alias:
            pairs.append(("db", "/var/lib/openldap"))
        result = []
        for purpose, target in pairs:
            setting = f"type=volume,source={self.volumes[purpose]},target={target}"
            if purpose == "source" and readonly_source:
                setting += ",readonly"
            result += ["--mount", setting]
        return result

    @staticmethod
    def restricted():
        return [
            "--user",
            "389:389",
            "--read-only",
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--ulimit",
            "nofile=1024:1024",
        ]

    def exec(self, *arguments, data=None, check=True):
        return docker(
            "exec", "--interactive", self.container, *arguments, data=data, check=check
        )

    def start_server(self):
        docker(
            "exec",
            "--detach",
            self.container,
            "/bin/sh",
            "/source/run-slapd.sh",
            "-F",
            "/data/config",
            "-h",
            URI + "/ ldapi:///",
            "-d",
            "256",
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if (
                self.exec(
                    "ldapwhoami",
                    "-x",
                    "-H",
                    URI,
                    "-D",
                    ADMIN,
                    "-w",
                    PASSWORD,
                    check=False,
                ).returncode
                == 0
            ):
                return
            time.sleep(0.1)
        self.fail("fixture LDAP did not start")

    def stop_server(self):
        self.exec("/bin/sh", "-ec", 'kill -TERM "$(cat /run/openldap/run/slapd.pid)"')
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if (
                self.exec(
                    "flock", "-n", "/data/.slapd.lock", "true", check=False
                ).returncode
                == 0
            ):
                return
            time.sleep(0.1)
        self.fail("daemon lifecycle lock was not released after shutdown")

    def init_arguments(self, alias=True):
        name = self.prefix + "-init-" + uuid.uuid4().hex[:6]
        self.addCleanup(docker, "rm", "--force", name, check=False)
        return [
            "run",
            "--rm",
            "--name",
            name,
            *self.restricted(),
            *self.mounts(alias),
            "--entrypoint",
            "/bin/sh",
            self.image,
            "/source/configure-access.sh",
        ]

    def export(self, database):
        return self.exec("slapcat", "-F", "/data/config", "-n", str(database)).stdout

    def search(self, bind, target, *requested, check=True):
        result = self.exec(
            "ldapsearch",
            "-LLL",
            "-o",
            "ldif-wrap=no",
            "-x",
            "-H",
            URI,
            "-D",
            bind,
            "-w",
            PASSWORD,
            "-b",
            target,
            "-s",
            "base",
            "(objectClass=*)",
            *requested,
            check=check,
        )
        if not check:
            self.assertIn(result.returncode, (0, 32), result.stderr.decode())
        return result.stdout

    def test_policy_is_idempotent_and_limits_photos_without_breaking_password_login(
        self,
    ):
        self.stop_server()
        directory = self.export(1)
        # Native cn=config tools open olcDbDirectory even for database zero.
        self.assertNotEqual(
            docker(*self.init_arguments(alias=False), check=False).returncode, 0
        )
        docker(*self.init_arguments())
        first = attributes(self.export(0), "olcAccess")
        docker(*self.init_arguments())
        self.assertEqual(attributes(self.export(0), "olcAccess"), first)
        self.assertEqual(self.export(1), directory)
        self.start_server()

        alice = "uid=alice,ou=users," + BASE
        bob = "uid=bob,ou=users," + BASE
        self.exec("ldapwhoami", "-x", "-H", URI, "-D", alice, "-w", PASSWORD)
        self.assertNotEqual(
            self.exec(
                "ldapwhoami",
                "-x",
                "-H",
                URI,
                "-D",
                alice,
                "-w",
                "wrong-fixture-password",
                check=False,
            ).returncode,
            0,
        )
        member = self.search(SERVICE, alice, "uid", "jpegPhoto", "userPassword")
        self.assertEqual(attributes(member, "jpegPhoto"), [PHOTO])
        self.assertEqual(attributes(member, "userPassword"), [])
        self.assertEqual(
            attributes(
                self.search(SERVICE, bob, "uid", "jpegPhoto", check=False), "jpegPhoto"
            ),
            [],
        )
        self.assertEqual(
            attributes(self.search(ADMIN, alice, "userPassword"), "userPassword"),
            [PASSWORD.encode()],
        )
        found = self.exec(
            "ldapsearch",
            "-LLL",
            "-x",
            "-H",
            URI,
            "-D",
            SERVICE,
            "-w",
            PASSWORD,
            "-b",
            "ou=users," + BASE,
            f"(|(memberOf={GROUP})(memberOf={ADMIN_GROUP}))",
            "uid",
        ).stdout
        self.assertCountEqual(attributes(found, "uid"), [b"alice", b"charlie"])
        administrators = self.exec(
            "ldapsearch",
            "-LLL",
            "-x",
            "-H",
            URI,
            "-D",
            SERVICE,
            "-w",
            PASSWORD,
            "-b",
            "ou=users," + BASE,
            f"(memberOf={ADMIN_GROUP})",
            "uid",
        ).stdout
        self.assertEqual(attributes(administrators, "uid"), [b"charlie"])


if __name__ == "__main__":
    unittest.main()
