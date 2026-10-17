# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT
"""Opt-in Docker/OpenLDAP behavior test; all directory credentials are synthetic."""

import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from configuration import ConfigurationError, Reconciler

LDAP_ID = "958aad66-3784-4d2a-b89a-a7b6fab6e25c"
SSO_ID = "505ce9d1-d916-42fa-86ca-673ef241d7df"
LDAP_PROVIDER = "Jellyfin.Plugin.LDAP_Auth.LdapAuthenticationProviderPlugin"
PASSWORD = "disposable-integration-credential"


def command(*args, **kwargs):
    return subprocess.run(
        args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs
    ).stdout.decode()


@unittest.skipUnless(
    os.getenv("JELLYFIN_INTEGRATION_IMAGE"),
    "set JELLYFIN_INTEGRATION_IMAGE to run disposable Docker/OpenLDAP tests",
)
class IdentityIntegration(unittest.TestCase):
    def test_sso_created_user_receives_ldap_photo_before_password_login(self):
        temporary = tempfile.TemporaryDirectory(prefix="ops-jellyfin-identity-")
        self.addCleanup(temporary.cleanup)
        work = Path(temporary.name)
        gateway = json.loads(command("docker", "network", "inspect", "bridge"))[0][
            "IPAM"
        ]["Config"][0]["Gateway"]
        with socket.socket() as listener:
            listener.bind((gateway, 0))
            ldap_port = listener.getsockname()[1]
        ldap_dir = work / "ldap"
        ldap_dir.mkdir()
        config = work / "slapd.conf"
        schema = Path(os.getenv("LDAP_SCHEMA_DIR", "/etc/openldap/schema"))
        if not schema.exists():
            schema = Path("/etc/ldap/schema")
        modules = ""
        if Path("/usr/lib/ldap/back_mdb.so").exists():
            modules = "modulepath /usr/lib/ldap\nmoduleload back_mdb\n"
        config.write_text(f"""{modules}include {schema}/core.schema
include {schema}/cosine.schema
include {schema}/inetorgperson.schema
pidfile {work}/slapd.pid
argsfile {work}/slapd.args
database mdb
maxsize 10485760
suffix "dc=example,dc=test"
rootdn "cn=admin,dc=example,dc=test"
rootpw {PASSWORD}
directory {ldap_dir}
access to * by dn.exact="cn=admin,dc=example,dc=test" manage by anonymous auth by self read by * none
""")
        ldap_url = f"ldap://{gateway}:{ldap_port}"
        log = (work / "slapd.log").open("wb")
        slapd = subprocess.Popen(
            [
                "sh",
                "-c",
                'ulimit -n 1024; exec "$@"',
                "fixture",
                "slapd",
                "-f",
                str(config),
                "-h",
                ldap_url,
                "-d",
                "0",
            ],
            stdout=log,
            stderr=log,
        )
        self.addCleanup(log.close)
        self.addCleanup(lambda: slapd.wait(timeout=10))
        self.addCleanup(slapd.kill)
        for _ in range(100):
            try:
                with socket.create_connection((gateway, ldap_port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
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
userPassword: {PASSWORD}
jpegPhoto:: {base64.b64encode(jpeg).decode()}

dn: uid=bob,ou=users,dc=example,dc=test
objectClass: inetOrgPerson
uid: bob
cn: bob
sn: Fixture
jpegPhoto:: {base64.b64encode(jpeg).decode()}

dn: cn=collision-one,ou=users,dc=example,dc=test
objectClass: inetOrgPerson
cn: collision-one
uid: ambiguous
sn: Fixture
jpegPhoto:: {base64.b64encode(jpeg).decode()}

dn: cn=collision-two,ou=users,dc=example,dc=test
objectClass: inetOrgPerson
cn: collision-two
uid: ambiguous
sn: Fixture
jpegPhoto:: {base64.b64encode(jpeg).decode()}

"""
        command(
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
            "--user",
            "1000:1000",
            "--read-only",
            "--tmpfs",
            "/config:uid=1000,gid=1000",
            "--tmpfs",
            "/cache:uid=1000,gid=1000",
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
        self.addCleanup(
            lambda: Path("/tmp/ops-jellyfin-fixture.log").write_bytes(
                subprocess.run(
                    ["docker", "logs", container],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                ).stdout
            )
        )
        binding = json.loads(command("docker", "inspect", container))[0][
            "NetworkSettings"
        ]["Ports"]["8096/tcp"][0]
        url = f"http://127.0.0.1:{binding['HostPort']}"
        credentials = work / "credentials"
        credentials.mkdir()
        for name in (
            "LDAP_BIND_PASSWORD",
            "OIDC_CLIENT_SECRET",
            "JELLYFIN_ADMIN_PASSWORD",
        ):
            (credentials / name).write_text(PASSWORD)
        desired = {
            "baseURL": url,
            "adminUsername": "ops-recovery",
            "startup": {
                "configuration": {
                    "UICulture": "en-US",
                    "MetadataCountryCode": "US",
                    "PreferredMetadataLanguage": "en",
                },
                "remoteAccess": {
                    "EnableRemoteAccess": True,
                    "EnableAutomaticPortMapping": False,
                },
            },
            "resources": [
                {
                    "path": f"/Plugins/{LDAP_ID}/Configuration",
                    "values": {
                        "LdapServer": gateway,
                        "LdapPort": ldap_port,
                        "UseSsl": False,
                        "UseStartTls": False,
                        "SkipSslVerify": False,
                        "LdapBaseDn": "ou=users,dc=example,dc=test",
                        "LdapBindUser": "cn=admin,dc=example,dc=test",
                        "LdapSearchFilter": "(objectClass=inetOrgPerson)",
                        "LdapSearchAttributes": "uid",
                        "LdapUidAttribute": "uid",
                        "LdapUsernameAttribute": "uid",
                        "LdapAdminFilter": "_disabled_",
                        "CreateUsersFromLdap": True,
                        "EnableLdapProfileImageSync": True,
                        "LdapProfileImageAttribute": "jpegPhoto",
                        "LdapProfileImageFormat": "Binary",
                        "RemoveImagesNotInLdap": False,
                    },
                    "secretValues": [
                        {"path": ["LdapBindPassword"], "file": "LDAP_BIND_PASSWORD"}
                    ],
                },
                {
                    "path": f"/Plugins/{SSO_ID}/Configuration",
                    "values": {
                        "OidConfigs": {
                            "canaille": {
                                "Enabled": True,
                                "OidEndpoint": "https://auth.example.test",
                                "OidClientId": "fixture.jellyfin",
                                "DefaultProvider": LDAP_PROVIDER,
                                "DefaultUsernameClaim": "preferred_username",
                            }
                        }
                    },
                    "secretValues": [
                        {
                            "path": ["OidConfigs", "canaille", "OidSecret"],
                            "file": "OIDC_CLIENT_SECRET",
                        }
                    ],
                },
            ],
        }
        desired["libraries"] = [
            {
                "name": "Movies",
                "collectionType": "movies",
                "paths": ["/media/movies"],
            },
            {"name": "TV", "collectionType": "tvshows", "paths": ["/media/tv"]},
            {"name": "Music", "collectionType": "music", "paths": ["/media/music"]},
        ]
        desired["resources"].extend(
            [
                {
                    "path": "/System/Configuration",
                    "values": {"PluginRepositories": []},
                },
                {
                    "path": "/System/Configuration/network",
                    "values": {"EnableUPnP": False, "EnableRemoteAccess": True},
                },
                {
                    "path": "/System/Configuration/branding",
                    "values": {
                        "LoginDisclaimer": '<form action="/sso/OID/start/canaille"><button>Sign in with SSO</button></form>'
                    },
                },
            ]
        )
        desired_path = work / "desired.json"
        desired_path.write_text(json.dumps(desired))
        reconciler = Reconciler(desired_path, credentials)
        for _ in range(240):
            try:
                with urllib.request.urlopen(url + "/health", timeout=1):
                    break
            except OSError:
                status = command(
                    "docker", "inspect", "--format", "{{.State.Status}}", container
                ).strip()
                if status == "exited":
                    raise AssertionError(
                        "fixture Jellyfin exited: "
                        + command("docker", "logs", container)[-4000:]
                    )
                time.sleep(0.5)
        else:
            self.fail("Jellyfin did not become healthy")
        for _ in range(120):
            try:
                reconciler.run_once()
                break
            except ConfigurationError as error:
                if "503" not in str(error) and str(error) != "API request failed":
                    raise
                time.sleep(0.5)
        else:
            self.fail("Jellyfin startup APIs remained unavailable")
        libraries = reconciler.request("GET", "/Library/VirtualFolders")
        self.assertEqual(
            {item["Name"]: item["Locations"] for item in libraries},
            {
                "Movies": ["/media/movies"],
                "TV": ["/media/tv"],
                "Music": ["/media/music"],
            },
        )
        # Reproduce the exact SSO-first state: SSO creates a local user, then
        # assigns DefaultProvider. No LDAP password authentication has happened.
        reconciler.request("POST", "/Users/New", {"Name": "bob"})
        ambiguous = reconciler.request("POST", "/Users/New", {"Name": "ambiguous"})
        ambiguous_policy = ambiguous["Policy"]
        ambiguous_policy["AuthenticationProviderId"] = LDAP_PROVIDER
        reconciler.request("POST", f"/Users/{ambiguous['Id']}/Policy", ambiguous_policy)
        user = reconciler.request("POST", "/Users/New", {"Name": "alice"})
        user_id = user["Id"]
        policy = user["Policy"]
        policy["AuthenticationProviderId"] = LDAP_PROVIDER
        reconciler.request("POST", f"/Users/{user_id}/Policy", policy)
        ldap_path = f"/Plugins/{LDAP_ID}/Configuration"
        self.assertEqual(reconciler.request("GET", ldap_path)["LdapUsers"], [])
        tasks = reconciler.request("GET", "/ScheduledTasks")
        task = next(task for task in tasks if task["Key"] == "LdapProfileImageSync")
        reconciler.request("POST", f"/ScheduledTasks/Running/{task['Id']}")
        image = None
        for _ in range(120):
            try:
                request = urllib.request.Request(
                    url + f"/Users/{user_id}/Images/Primary",
                    headers={"X-Emby-Token": reconciler.token},
                )
                with urllib.request.urlopen(request, timeout=2) as response:
                    image = response.read()
                if image:
                    break
            except urllib.error.HTTPError as error:
                error.close()
            except OSError:
                pass
            time.sleep(0.25)
        self.assertIsNotNone(image, "SSO-first user never received an LDAP photo")
        self.assertEqual(image[:2], b"\xff\xd8")
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
        self.assertEqual(
            reconciler.request("GET", f"/Users/{user_id}")["Policy"][
                "AuthenticationProviderId"
            ],
            LDAP_PROVIDER,
        )
        reconciler.run_once(check_only=True)


if __name__ == "__main__":
    unittest.main()
