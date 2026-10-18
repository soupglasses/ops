# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT
"""Opt-in Docker/OpenLDAP behavior test; all directory credentials are synthetic."""

import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from provider_fixture import ProviderFixture
from health import ready

LDAP_ID = "958aad66-3784-4d2a-b89a-a7b6fab6e25c"
LDAP_PROVIDER = "Jellyfin.Plugin.LDAP_Auth.LdapAuthenticationProviderPlugin"
PASSWORD = "disposable-integration-credential"


def command(*args, **kwargs):
    try:
        return subprocess.run(
            args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs
        ).stdout.decode()
    except subprocess.CalledProcessError as error:
        raise RuntimeError(error.stderr.decode(errors="replace")) from error


@unittest.skipUnless(
    os.getenv("JELLYFIN_INTEGRATION_IMAGE")
    and os.getenv("JELLYFIN_PROVIDER_TEST_IMAGE"),
    "set JELLYFIN_INTEGRATION_IMAGE and JELLYFIN_PROVIDER_TEST_IMAGE for Docker/OpenLDAP tests",
)
class IdentityIntegration(unittest.TestCase):
    def test_ldap_password_login_and_upstream_scheduled_photo_sync(self):
        temporary = tempfile.TemporaryDirectory(prefix="ops-jellyfin-identity-")
        self.addCleanup(temporary.cleanup)
        work = Path(temporary.name)
        image = os.environ["JELLYFIN_INTEGRATION_IMAGE"]
        fixture_image = "ops-jellyfin-ldap-fixture:integration"
        # Keep the fixture in Docker as well: host slapd module paths and mandatory
        # access-control profiles differ between developer machines and CI runners.
        dockerfile = b"""# SPDX-License-Identifier: MIT
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER root
RUN echo 'slapd slapd/no_configuration boolean true' | debconf-set-selections \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends slapd ldap-utils \
    && rm -rf /var/lib/apt/lists/*
ENTRYPOINT ["slapd"]
"""
        command(
            "docker",
            "build",
            "--build-arg",
            "BASE_IMAGE=" + image,
            "--tag",
            fixture_image,
            "-",
            input=dockerfile,
        )
        network = "ops-jellyfin-identity-" + str(os.getpid())
        command("docker", "network", "create", network)
        self.addCleanup(lambda: command("docker", "network", "rm", network))
        ldap_dir = work / "ldap"
        ldap_dir.mkdir()
        config = work / "slapd.conf"
        config.write_text(f"""include /etc/ldap/schema/core.schema
include /etc/ldap/schema/cosine.schema
include /etc/ldap/schema/inetorgperson.schema
modulepath /usr/lib/ldap
moduleload back_mdb
pidfile /fixture/slapd.pid
argsfile /fixture/slapd.args
database mdb
maxsize 10485760
suffix "dc=example,dc=test"
rootdn "cn=admin,dc=example,dc=test"
rootpw {PASSWORD}
directory /fixture/ldap
access to * by dn.exact="cn=admin,dc=example,dc=test" manage by anonymous auth by self read by * none
""")
        ldap_container = command(
            "docker",
            "run",
            "--detach",
            "--network",
            network,
            "--network-alias",
            "ldap",
            "--ulimit",
            "nofile=1024:1024",
            "--volume",
            str(work) + ":/fixture",
            fixture_image,
            "-f",
            "/fixture/slapd.conf",
            "-h",
            "ldap://0.0.0.0:1389",
            "-d",
            "256",
        ).strip()
        self.addCleanup(lambda: command("docker", "rm", "--force", ldap_container))
        ldap_url = "ldap://127.0.0.1:1389"
        probe = [
            "docker",
            "exec",
            ldap_container,
            "ldapwhoami",
            "-x",
            "-H",
            ldap_url,
            "-D",
            "cn=admin,dc=example,dc=test",
            "-w",
            PASSWORD,
        ]
        for _ in range(150):
            result = subprocess.run(probe, capture_output=True)
            if result.returncode == 0:
                break
            status = command(
                "docker", "inspect", "--format", "{{.State.Status}}", ldap_container
            ).strip()
            if status == "exited":
                break
            time.sleep(0.2)
        else:
            status = "readiness timeout"
        if result.returncode:
            diagnostic = subprocess.run(
                ["docker", "logs", ldap_container], capture_output=True
            )
            self.fail(
                "LDAP fixture did not become ready ("
                + status
                + "): "
                + (diagnostic.stdout + diagnostic.stderr).decode(errors="replace")[
                    -5000:
                ]
            )
        jpeg = (Path(__file__).parent / "profile.jpg").read_bytes()
        ldif = f"""dn: dc=example,dc=test
objectClass: domain
dc: example

dn: ou=users,dc=example,dc=test
objectClass: organizationalUnit
ou: users

dn: uid=alice,ou=users,dc=example,dc=test
objectClass: inetOrgPerson
uid: alice
cn: alice
sn: Fixture
mail: alice@example.test
userPassword: {PASSWORD}
jpegPhoto:: {base64.b64encode(jpeg).decode()}

"""
        command(
            "docker",
            "exec",
            "--interactive",
            ldap_container,
            "ldapadd",
            "-x",
            "-H",
            ldap_url,
            "-D",
            "cn=admin,dc=example,dc=test",
            "-w",
            PASSWORD,
            input=ldif.encode(),
        )
        media = work / "media"
        for name in ("movies", "tv", "music"):
            (media / name).mkdir(parents=True)
        container = command(
            "docker",
            "run",
            "--detach",
            "--network",
            network,
            "--user",
            "1000:1000",
            "--read-only",
            "--tmpfs",
            "/config:uid=1000,gid=1000",
            "--tmpfs",
            "/cache:uid=1000,gid=1000",
            "--tmpfs",
            "/backup:uid=1000,gid=1000",
            "--tmpfs",
            "/run/jellyfin:uid=1000,gid=1000",
            "--tmpfs",
            "/tmp:uid=1000,gid=1000",
            "--volume",
            str(media) + ":/media:ro",
            "--publish",
            "127.0.0.1::8096",
            "--name",
            f"ops-jellyfin-identity-{os.getpid()}",
            os.environ["JELLYFIN_INTEGRATION_IMAGE"],
        ).strip()
        self.addCleanup(lambda: command("docker", "rm", "--force", container))

        def fixture_diagnostics():
            logs = subprocess.run(
                ["docker", "logs", container],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            ).stdout
            Path("/tmp/ops-jellyfin-fixture.log").write_bytes(logs)
            result = self._outcome.result
            if any(test is self for test, _ in result.errors + result.failures):
                print(
                    "Synthetic Jellyfin fixture log:\n"
                    + logs.decode(errors="replace")[-16000:],
                    file=sys.stderr,
                )

        self.addCleanup(fixture_diagnostics)
        binding = json.loads(command("docker", "inspect", container))[0][
            "NetworkSettings"
        ]["Ports"]["8096/tcp"][0]
        url = f"http://127.0.0.1:{binding['HostPort']}"
        credentials = work / "credentials"
        credentials.mkdir()
        for name in (
            "LDAP_BIND_PASSWORD",
            "JELLYFIN_ADMIN_PASSWORD",
        ):
            (credentials / name).write_text(PASSWORD)
        desired = {
            "baseURL": url,
            "adminUsername": "ops-recovery",
            "ldapConfiguration": {
                "LdapServer": "ldap",
                "LdapPort": 1389,
                "UseSsl": False,
                "UseStartTls": False,
                "SkipSslVerify": False,
                "LdapBaseDn": "ou=users,dc=example,dc=test",
                "LdapBindUser": "cn=admin,dc=example,dc=test",
                "LdapSearchFilter": "(objectClass=inetOrgPerson)",
                "LdapSearchAttributes": "uid,mail",
                "LdapUidAttribute": "uid",
                "LdapUsernameAttribute": "uid",
                "LdapAdminFilter": "(uid=alice)",
                "CreateUsersFromLdap": True,
                "AllowPassChange": False,
                "EnableLdapProfileImageSync": True,
                "LdapProfileImageAttribute": "jpegPhoto",
                "LdapProfileImageFormat": "Binary",
                "RemoveImagesNotInLdap": False,
            },
            "libraries": [
                {
                    "name": "Movies",
                    "collectionType": "movies",
                    "paths": ["/media/movies"],
                },
                {"name": "TV", "collectionType": "tvshows", "paths": ["/media/tv"]},
                {"name": "Music", "collectionType": "music", "paths": ["/media/music"]},
            ],
        }
        desired_path = work / "desired.json"
        desired_path.write_text(json.dumps(desired))
        reconciler = ProviderFixture(desired_path, credentials)
        for _ in range(240):
            if ready(url + "/health"):
                break
            status = command(
                "docker", "inspect", "--format", "{{.State.Status}}", container
            ).strip()
            if status == "exited":
                self.fail("fixture Jellyfin exited before becoming healthy")
            time.sleep(0.5)
        else:
            self.fail("Jellyfin did not finish startup (health body must be Healthy)")
        reconciler.run_once()
        libraries = reconciler.request("GET", "/Library/VirtualFolders")
        library_ids = {item["Name"]: item["ItemId"] for item in libraries}
        self.assertEqual(
            {item["Name"]: item["Locations"] for item in libraries},
            {
                "Movies": ["/media/movies"],
                "TV": ["/media/tv"],
                "Music": ["/media/music"],
            },
        )
        result = reconciler.request(
            "POST",
            "/Users/AuthenticateByName",
            {"Username": "alice@example.test", "Pw": PASSWORD},
            authenticated=False,
        )
        user_id = result["User"]["Id"]
        self.assertEqual(result["User"]["Name"], "alice")
        self.assertTrue(result["User"]["Policy"]["IsAdministrator"])
        ldap_path = f"/Plugins/{LDAP_ID}/Configuration"
        linked = reconciler.request("GET", ldap_path)["LdapUsers"]
        self.assertEqual(len(linked), 1)
        self.assertEqual(linked[0]["LdapUid"], "alice")
        tasks = reconciler.request("GET", "/ScheduledTasks")
        task = next(task for task in tasks if task["Key"] == "LdapProfileImageSync")
        reconciler.request("POST", f"/ScheduledTasks/Running/{task['Id']}")
        profile_image = None
        for _ in range(120):
            try:
                request = urllib.request.Request(
                    url + f"/Users/{user_id}/Images/Primary",
                    headers={
                        "Authorization": f'MediaBrowser Token="{reconciler.token}"'
                    },
                )
                with urllib.request.urlopen(request, timeout=2) as response:
                    profile_image = response.read()
                if profile_image:
                    break
            except urllib.error.HTTPError as error:
                error.close()
            except OSError:
                pass
            time.sleep(0.25)
        self.assertIsNotNone(profile_image, "LDAP user never received an LDAP photo")
        self.assertEqual(profile_image[:2], b"\xff\xd8")
        before = reconciler.request("GET", ldap_path)["LdapUsers"]
        self.assertEqual(len(before), 1)
        self.assertEqual(before[0]["LdapUid"], "alice")
        self.assertEqual(
            before[0]["ProfileImageHash"],
            base64.b64encode(hashlib.md5(jpeg).digest()).decode(),
        )
        observed = reconciler.request("GET", ldap_path)
        observed["LdapServer"] = "drift.invalid"
        reconciler.request("POST", ldap_path, observed)
        reconciler.run_once()
        self.assertEqual(reconciler.request("GET", ldap_path)["LdapUsers"], before)
        result = reconciler.request(
            "POST",
            "/Users/AuthenticateByName",
            {"Username": "alice", "Pw": PASSWORD},
            authenticated=False,
        )
        self.assertEqual(result["User"]["Id"], user_id)
        self.assertEqual(len(reconciler.request("GET", ldap_path)["LdapUsers"]), 1)
        self.assertEqual(
            reconciler.request("GET", f"/Users/{user_id}")["Policy"][
                "AuthenticationProviderId"
            ],
            LDAP_PROVIDER,
        )
        for admin_filter, expected in (("(uid=nobody)", False), ("(uid=alice)", True)):
            desired["ldapConfiguration"]["LdapAdminFilter"] = admin_filter
            desired_path.write_text(json.dumps(desired))
            reconciler.run_once()
            result = reconciler.request(
                "POST",
                "/Users/AuthenticateByName",
                {"Username": "alice", "Pw": PASSWORD},
                authenticated=False,
            )
            self.assertEqual(result["User"]["Id"], user_id)
            self.assertEqual(result["User"]["Policy"]["IsAdministrator"], expected)
        reconciler.run_once(check_only=True)
        self.assertEqual(
            {
                item["Name"]: item["ItemId"]
                for item in reconciler.request("GET", "/Library/VirtualFolders")
            },
            library_ids,
        )

        server_id = reconciler.request("GET", "/System/Info")["Id"]
        command(
            "docker",
            "exec",
            container,
            "python3",
            "/usr/local/lib/jellyfin/runtime.py",
            "checkpoint",
        )
        restored_volume = command("docker", "volume", "create").strip()
        self.addCleanup(lambda: command("docker", "volume", "rm", restored_volume))
        command("docker", "exec", container, "test", "-s", "/backup/checkpoint.tar")
        # Docker's archive endpoint does not expose this tmpfs-backed checkpoint.
        archive = subprocess.check_output(
            ["docker", "exec", container, "cat", "/backup/checkpoint.tar"]
        )
        # Model the PVC's ownership without depending on the host runner's UID.
        command(
            "docker",
            "run",
            "--rm",
            "--interactive",
            "--user",
            "0:0",
            "--volume",
            restored_volume + ":/config",
            "--entrypoint",
            "sh",
            image,
            "-c",
            "cat > /config/checkpoint.tar && chown 1000:1000 /config /config/checkpoint.tar",
            input=archive,
        )
        command(
            "docker",
            "run",
            "--rm",
            "--user",
            "1000:1000",
            "--volume",
            restored_volume + ":/config",
            "--entrypoint",
            "python3",
            image,
            "/usr/local/lib/jellyfin/runtime.py",
            "restore",
            "--source",
            "/config",
        )
        restored_container = command(
            "docker",
            "run",
            "--detach",
            "--network",
            network,
            "--user",
            "1000:1000",
            "--read-only",
            "--volume",
            restored_volume + ":/config",
            "--volume",
            str(media) + ":/media:ro",
            "--tmpfs",
            "/cache:uid=1000,gid=1000",
            "--tmpfs",
            "/backup:uid=1000,gid=1000",
            "--tmpfs",
            "/run/jellyfin:uid=1000,gid=1000",
            "--tmpfs",
            "/tmp:uid=1000,gid=1000",
            "--publish",
            "127.0.0.1::8096",
            image,
        ).strip()
        self.addCleanup(lambda: command("docker", "rm", "--force", restored_container))
        binding = json.loads(command("docker", "inspect", restored_container))[0][
            "NetworkSettings"
        ]["Ports"]["8096/tcp"][0]
        desired["baseURL"] = f"http://127.0.0.1:{binding['HostPort']}"
        desired_path.write_text(json.dumps(desired))
        for _ in range(240):
            if ready(desired["baseURL"] + "/health"):
                break
            time.sleep(0.5)
        else:
            self.fail("restored Jellyfin did not become healthy")
        restored = ProviderFixture(desired_path, credentials)
        restored.authenticate()
        self.assertEqual(restored.request("GET", "/System/Info")["Id"], server_id)
        self.assertEqual(restored.request("GET", ldap_path)["LdapUsers"], before)
        drifted = restored.request("GET", ldap_path)
        drifted["LdapServer"] = "drift.invalid"
        restored.request("POST", ldap_path, drifted)
        restored.run_once()
        restored.run_once(check_only=True)
        self.assertEqual(restored.request("GET", ldap_path)["LdapUsers"], before)
        self.assertEqual(
            {
                item["Name"]: item["ItemId"]
                for item in restored.request("GET", "/Library/VirtualFolders")
            },
            library_ids,
        )
        authenticated = restored.request(
            "POST",
            "/Users/AuthenticateByName",
            {"Username": "alice", "Pw": PASSWORD},
            authenticated=False,
        )
        self.assertEqual(authenticated["User"]["Id"], user_id)
        self.assertTrue(authenticated["User"]["Policy"]["IsAdministrator"])
        request = urllib.request.Request(
            desired["baseURL"] + f"/Users/{user_id}/Images/Primary",
            headers={"Authorization": f'MediaBrowser Token="{restored.token}"'},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.read(), profile_image)


if __name__ == "__main__":
    unittest.main()
