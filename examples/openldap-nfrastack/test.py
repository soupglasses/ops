#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
#
# SPDX-License-Identifier: 0BSD

"""Exercise the published image with synthetic data; never access live LDAP."""

from pathlib import Path
import argparse
import base64
import os
import secrets
import shutil
import subprocess
import tempfile
import time


HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
IMAGE = "docker.io/nfrastack/openldap:2.6-8.1.1@sha256:5e5c8fdd1d7e3a9623500839699b258d0569bf965528b85c682ec5d0902f5d6c"
OLD_IMAGE = "ghcr.io/soupglasses/openldap:2.6.10@sha256:ea891fd9cdaa941a3e7ddd8375319ccb4a63b4f0da418516a0393ad0527c8005"
SUFFIX = "dc=finnes,dc=dev"
ADMIN = f"cn=admin,{SUFFIX}"
READER = f"uid=nextcloud,ou=system,{SUFFIX}"
USER = f"uid=alice,ou=users,{SUFFIX}"


def run(*args, data=None, check=True, timeout=60):
    result = subprocess.run(args, input=data, text=True, capture_output=True, timeout=timeout)
    if check and result.returncode:
        # Do not echo command arguments, LDIF, or configuration dumps.
        raise RuntimeError(f"{args[0]} failed ({result.returncode}): {result.stderr[-2000:]}")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true",
                        help="retain synthetic data after a successful drill for Docker Compose")
    args = parser.parse_args()
    successful = False
    restricted_runtime = False
    token = secrets.token_hex(4)
    name = f"ops-ldap-test-{token}"
    network = f"{name}-net"
    root = Path(tempfile.mkdtemp(prefix="ops-ldap-nfrastack-"))
    root.chmod(0o755)
    for directory in ("data", "certs", "secrets", "schema", "bootstrap"):
        (root / directory).mkdir()
    for identity in ("admin", "config", "reader", "alice"):
        (root / "secrets" / identity).write_text(secrets.token_urlsafe(32))
        (root / "secrets" / identity).chmod(0o600)
    shutil.copyfile(REPO / "kubernetes/apps/identity/openldap/app/oauth.schema.conf",
                    root / "schema/oauth.schema")
    for path in (HERE / "bootstrap").glob("*.ldif"):
        shutil.copyfile(path, root / "bootstrap" / path.name)
    run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
        "-subj", "/CN=ldap.finnes.dev", "-addext", "subjectAltName=DNS:ldap.finnes.dev",
        "-keyout", str(root / "certs/tls.key"), "-out", str(root / "certs/tls.crt"))
    # Only disposable test key material is made readable to the image's ldap UID.
    (root / "certs/tls.key").chmod(0o644)
    shutil.copyfile(root / "certs/tls.crt", root / "certs/ca.crt")
    mounts = []
    for source, target, mode in (
        ("data", "/data", "rw"), ("certs", "/certs", "ro"),
        ("secrets", "/test-secrets", "ro"),
        ("schema", "/container/data/openldap/config/bootstrap/schema", "rw"),
        ("bootstrap", "/container/data/openldap/config/bootstrap/ldif/custom", "rw"),
    ):
        mounts += ["-v", f"{root / source}:{target}:{mode}"]

    def start():
        if restricted_runtime:
            run("docker", "run", "-d", "--name", name, "--hostname", "ldap.finnes.dev",
                "--network", network, "--network-alias", "ldap.finnes.dev",
                "--user", "389:389", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--read-only", "--entrypoint", "/usr/sbin/slapd", *mounts,
                "-v", f"{root / 'data/db'}:/var/lib/openldap",
                "-v", f"{root / 'run'}:/run/openldap", IMAGE,
                "-F", "/data/config", "-h", "ldaps:/// ldapi:///", "-d", "256")
            wait_ready()
            return
        capabilities = ["--cap-drop", "ALL"]
        for capability in ("CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID", "KILL",
                           "NET_BIND_SERVICE"):
            capabilities += ["--cap-add", capability]
        run("docker", "run", "-d", "--name", name, "--hostname", "ldap.finnes.dev",
            "--network", network, "--network-alias", "ldap.finnes.dev",
            "--env-file", str(HERE / "settings.env"), *capabilities, *mounts, IMAGE)
        wait_ready()

    def wait_ready():
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            result = ldap("ldapwhoami", check=False)
            if result.returncode == 0:
                return
            time.sleep(2)
        logs = run("docker", "logs", "--tail", "60", name, check=False)
        raise RuntimeError("LDAP bootstrap failed:\n" + logs.stdout + logs.stderr)

    def ldap(tool, *args, identity="admin", data=None, check=True):
        dn = {"admin": ADMIN, "reader": READER, "alice": USER}[identity]
        return run("docker", "exec", "-u", str(os.getuid()), "-i", "-e", "LDAPTLS_CACERT=/certs/ca.crt",
                   "-e", "LDAPTLS_REQCERT=demand", name,
                   tool, "-x", "-H", "ldaps://ldap.finnes.dev", "-D", dn,
                   "-y", f"/test-secrets/{identity}", *args,
                   data=data, check=check, timeout=10)

    def verify():
        assert ldap("ldapwhoami", identity="reader").stdout.strip() == f"dn:{READER}"
        assert ldap("ldapwhoami", identity="alice").stdout.strip() == f"dn:{USER}"
        people = ldap("ldapsearch", "-LLL", "-b", f"ou=users,{SUFFIX}",
                      "(uid=alice)", "*", "+", identity="reader").stdout
        assert "uid: alice" in people and "memberOf:" in people
        assert all(key not in people for key in ("userPassword:", "oathSecret:", "pwdHistory:"))
        suffix_search = ldap("ldapsearch", "-LLL", "-b", SUFFIX,
                             "(uid=alice)", "uid", "entryUUID", identity="reader").stdout
        assert "uid: alice" in suffix_search and "entryUUID:" in suffix_search
        otp = ldap("ldapsearch", "-LLL", "-b", f"uid=mfa,ou=users,{SUFFIX}",
                   "-s", "base", "oathSecret", identity="reader").stdout
        assert "oathSecret:" not in otp
        otp_admin = ldap("ldapsearch", "-LLL", "-b", f"uid=mfa,ou=users,{SUFFIX}",
                         "-s", "base", "oathSecret").stdout
        assert "synthetic-otp-secret" in otp_admin
        oauth_admin = ldap("ldapsearch", "-LLL", "-b", f"oauthClientID=test,ou=oauth,{SUFFIX}",
                           "-s", "base", "oauthClientSecret").stdout
        assert "oauthClientSecret: synthetic-test-only" in oauth_admin
        password = ldap("ldapsearch", "-LLL", "-o", "ldif-wrap=no", "-b", READER,
                        "-s", "base", "userPassword").stdout
        encoded = next(line.split(":: ", 1)[1] for line in password.splitlines()
                       if line.startswith("userPassword:: "))
        assert base64.b64decode(encoded).startswith(b"{ARGON2}"), "password is not Argon2 hashed"
        groups = ldap("ldapsearch", "-LLL", "-b", f"cn=nextcloud-users,ou=groups,{SUFFIX}",
                      "-s", "base", "member", identity="reader").stdout
        assert f"member: {USER}" in groups
        denied = ldap("ldapmodify", identity="reader", check=False, data=(
            f"dn: {USER}\nchangetype: modify\nreplace: sn\nsn: Forbidden\n"))
        assert denied.returncode == 50, "reader unexpectedly acquired write access"
        oauth = ldap("ldapsearch", "-LLL", "-b", f"ou=oauth,{SUFFIX}",
                     "(objectClass=*)", "*", identity="reader", check=False)
        assert "dn:" not in oauth.stdout and "oauthClientSecret:" not in oauth.stdout
        # Test the socket using kernel credentials, not a configuration password.
        config = run("docker", "exec", "-u", "0", name, "ldapsearch", "-LLL", "-Q", "-Y", "EXTERNAL",
                     "-H", "ldapi:///", "-b", "cn=config", "(objectClass=olcGlobal)",
                     "olcTLSProtocolMin", "olcSecurity").stdout
        assert "olcTLSProtocolMin: 3.3" in config and "ssf=128" in config
        # Verify the network endpoint from a separate container, including that
        # port 389 cannot be used. No host port or live cluster is involved.
        for uri, expected in (("ldap://ldap.finnes.dev", False),
                              ("ldaps://ldap.finnes.dev", True)):
            result = run("docker", "run", "--rm", "--network", network,
                         "--entrypoint", "ldapwhoami", "-v", f"{root / 'certs'}:/certs:ro",
                         "-v", f"{root / 'secrets'}:/test-secrets:ro",
                         "-e", "LDAPTLS_CACERT=/certs/ca.crt", "-e", "LDAPTLS_REQCERT=demand",
                         IMAGE, "-x", "-o", "nettimeout=3", "-H", uri,
                         "-D", READER, "-y", "/test-secrets/reader", check=False, timeout=15)
            assert (result.returncode == 0) == expected, f"unexpected listener: {uri}"

    def verify_renewal():
        def served_serial():
            cert = run("docker", "exec", name, "openssl", "s_client", "-connect",
                       "ldap.finnes.dev:636", "-servername", "ldap.finnes.dev", "-CAfile",
                       "/certs/ca.crt", "-verify_return_error", data="").stdout
            return run("openssl", "x509", "-noout", "-serial", data=cert).stdout

        original = served_serial()
        run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
            "-subj", "/CN=ldap.finnes.dev", "-addext", "subjectAltName=DNS:ldap.finnes.dev",
            "-keyout", str(root / "certs/next.key"), "-out", str(root / "certs/next.crt"))
        (root / "certs/next.key").chmod(0o644)
        # Trust both synthetic certificates while validating the transition.
        (root / "certs/ca.crt").write_text((root / "certs/tls.crt").read_text()
                                         + (root / "certs/next.crt").read_text())
        (root / "certs/next.key").replace(root / "certs/tls.key")
        (root / "certs/next.crt").replace(root / "certs/tls.crt")
        assert served_serial() == original, "test expected the old in-memory TLS context"
        run("docker", "exec", "-u", "389:389", "-i", name, "sh", "-s", "--", "--once",
            data=(HERE / "reload-tls.sh").read_text())
        expected = run("openssl", "x509", "-in", str(root / "certs/tls.crt"),
                       "-noout", "-serial").stdout
        assert served_serial() == expected and expected != original
        verify()
        print("PASS: certificate replacement + local reload serves the renewed certificate", flush=True)

    def verify_watcher():
        certs = root / "certs"
        previous = certs / "projected-old"
        renewed = certs / "projected-new"
        previous.mkdir()
        renewed.mkdir()
        for filename in ("tls.crt", "tls.key"):
            shutil.copyfile(certs / filename, previous / filename)
            (certs / filename).unlink()
            (certs / filename).symlink_to(f"..data/{filename}")
        (certs / "..data").symlink_to(previous.name)
        run("docker", "run", "-d", "--name", name + "-tls", "--network", "none",
            "--user", "389:389", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--read-only", "--entrypoint", "/bin/sh",
            "-v", f"{root / 'run'}:/run/openldap", "-v", f"{certs}:/certs:ro",
            "-v", f"{HERE / 'reload-tls.sh'}:/source/reload-tls.sh:ro",
            IMAGE, "/source/reload-tls.sh")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if 'modifying entry "cn=config"' in run("docker", "logs", name + "-tls").stdout:
                break
            time.sleep(1)
        else:
            raise AssertionError("certificate watcher did not initialize")
        run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
            "-subj", "/CN=ldap.finnes.dev", "-addext", "subjectAltName=DNS:ldap.finnes.dev",
            "-keyout", str(renewed / "tls.key"), "-out", str(renewed / "tls.crt"))
        (renewed / "tls.key").chmod(0o644)
        (certs / "ca.crt").write_text((previous / "tls.crt").read_text()
                                     + (renewed / "tls.crt").read_text())
        # Match Kubernetes' atomic projected-Secret directory switch.
        (certs / "..data-new").symlink_to(renewed.name)
        (certs / "..data-new").replace(certs / "..data")
        expected = run("openssl", "x509", "-in", str(renewed / "tls.crt"),
                       "-noout", "-serial").stdout
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            cert = run("docker", "exec", name, "openssl", "s_client", "-connect",
                       "ldap.finnes.dev:636", "-servername", "ldap.finnes.dev", "-CAfile",
                       "/certs/ca.crt", "-verify_return_error", data="").stdout
            if run("openssl", "x509", "-noout", "-serial", data=cert).stdout == expected:
                break
            time.sleep(2)
        else:
            raise AssertionError("watcher did not serve projected certificate within 45 seconds")
        run("docker", "rm", "-f", name + "-tls")
        verify()
        print("PASS: isolated certificate watcher follows projected-Secret symlink rotation", flush=True)

    try:
        run("docker", "network", "create", "--internal", network)
        # Upstream update_template preserves ownership via sudo. Host UID 1000
        # need not exist in the image, so stage bootstrap files as its ldap user.
        run("docker", "run", "--rm", "--network", "none", "--entrypoint", "/bin/sh",
            *mounts, IMAGE, "-ec", "chown -R 389:389 "
            "/container/data/openldap/config/bootstrap/schema "
            "/container/data/openldap/config/bootstrap/ldif/custom")
        start()
        fixture = f"""dn: {SUFFIX}
objectClass: dcObject
objectClass: organization
dc: finnes
o: Finnes test

"""
        for ou in ("users", "groups", "system", "policies", "oauth"):
            fixture += f"dn: ou={ou},{SUFFIX}\nobjectClass: organizationalUnit\nou: {ou}\n\n"
        # Match the distinction already declared by tofu/ldap/base/policies.tf.
        for policy, must_change, lockout, allow_change, failures in (
            ("password", "TRUE", "TRUE", "TRUE", 999),
            ("system", "FALSE", "FALSE", "FALSE", 0),
        ):
            fixture += (f"dn: cn={policy},ou=policies,{SUFFIX}\nobjectClass: person\n"
                        f"objectClass: pwdPolicy\ncn: {policy}\nsn: {policy}\n"
                        f"pwdAttribute: userPassword\npwdMustChange: {must_change}\n"
                        f"pwdLockout: {lockout}\npwdAllowUserChange: {allow_change}\n"
                        f"pwdMaxFailure: {failures}\n\n")
        for dn, uid, password, extra in (
            (READER, "nextcloud", "reader", f"pwdPolicySubentry: cn=system,ou=policies,{SUFFIX}\n"),
            (USER, "alice", "alice", "objectClass: oathHOTPToken\n"),
        ):
            fixture += (f"dn: {dn}\nobjectClass: inetOrgPerson\n{extra}uid: {uid}\n"
                        f"cn: {uid}\nsn: Test\nuserPassword: "
                        f"{(root / 'secrets' / password).read_text()}\n\n")
        for group, member in (("ldap-readers", READER), ("nextcloud-users", USER)):
            fixture += (f"dn: cn={group},ou=groups,{SUFFIX}\nobjectClass: groupOfNames\n"
                        f"cn: {group}\nmember: {member}\n\n")
        fixture += (f"dn: oauthClientID=test,ou=oauth,{SUFFIX}\nobjectClass: oauthClient\n"
                    "oauthClientID: test\noauthClientSecret: synthetic-test-only\n\n"
                    f"dn: uid=mfa,ou=users,{SUFFIX}\nobjectClass: inetOrgPerson\n"
                    "objectClass: oathHOTPToken\nuid: mfa\ncn: MFA\nsn: Test\n"
                    "oathSecret: synthetic-otp-secret\n")
        ldap("ldapadd", data=fixture)
        # Existing migrated people have completed enrollment. Exercise the normal
        # account state rather than pretending an admin-set initial password is final.
        ldap("ldapmodify", data=f"dn: {USER}\nchangetype: modify\nreplace: pwdReset\npwdReset: FALSE\n")
        verify()
        print("PASS: OAuth/OTP schemas, password binds, reader ACLs, verified LDAPS, port 389 closed",
              flush=True)
        verify_renewal()
        run("docker", "rm", "-f", name)
        start()
        verify()
        print("PASS: container replacement preserves configuration and accounts", flush=True)
        for number, filename in ((0, "config.ldif"), (1, "directory.ldif")):
            dump = run("docker", "exec", name, "slapcat", "-F", "/data/config", "-n", str(number))
            (root / filename).write_text(dump.stdout)
            (root / filename).chmod(0o600)
        run("docker", "rm", "-f", name)
        (root / "data").rename(root / "rollback-data")
        (root / "data/config").mkdir(parents=True)
        (root / "data/db").mkdir()
        run("docker", "run", "--rm", "--network", "none", "--entrypoint", "/bin/sh",
            *mounts, "-v", f"{root}:/restore:ro", IMAGE, "-ec",
            "rm -rf /var/lib/openldap; ln -s /data/db /var/lib/openldap; "
            "slapadd -F /data/config -n 0 -l /restore/config.ldif; "
            "slapadd -F /data/config -n 1 -l /restore/directory.ldif; "
            "chown -R 389:389 /data/config /data/db", timeout=60)
        start()
        verify()
        print("PASS: fresh data/config restore preserves authentication, groups, and ACLs", flush=True)
        run("docker", "rm", "-f", name)

        # A portable directory export is the one-time migration boundary. Keep
        # Nfrastack's newly generated config rather than importing the old server config.
        (root / "old-data").mkdir()
        run("docker", "run", "--rm", "--network", "none", "--entrypoint", "/bin/sh",
            "-v", f"{root / 'old-data'}:/old", IMAGE, "-ec", "chown 65534:65534 /old")
        run("docker", "run", "-d", "--name", name, "--hostname", "ldap.finnes.dev",
            "--network", network, "--network-alias", "ldap.finnes.dev",
            "-e", f"LDAP_SUFFIX={SUFFIX}", "-e", f"LDAP_ROOT_DN={ADMIN}",
            "-e", f"LDAP_ROOT_PASSWORD={(root / 'secrets/admin').read_text()}",
            "-e", "LDAP_URLS=ldaps://0.0.0.0:636/", "-e", "LDAP_OVERLAYS=memberof,refint,otp",
            "-v", f"{root / 'old-data'}:/config/data",
            "-v", f"{root / 'certs'}:/config/tls:ro",
            "-v", f"{root / 'certs'}:/certs:ro",
            "-v", f"{root / 'secrets'}:/test-secrets:ro",
            "-v", f"{root / 'schema/oauth.schema'}:/config/slapd.conf.d/oauth.conf:ro", OLD_IMAGE)
        wait_ready()
        ldap("ldapadd", data=fixture.split("\n\n", 1)[1])
        before = ldap("ldapsearch", "-LLL", "-o", "ldif-wrap=no", "-b", READER,
                      "-s", "base", "entryUUID", "userPassword").stdout
        identities_before = ldap("ldapsearch", "-LLL", "-o", "ldif-wrap=no",
                                 "-b", SUFFIX, "(objectClass=*)", "entryUUID").stdout
        dump = run("docker", "exec", name, "slapcat", "-f", "/config/slapd.conf")
        (root / "old-directory.ldif").write_text(dump.stdout)
        (root / "old-directory.ldif").chmod(0o600)
        run("docker", "rm", "-f", name)
        run("docker", "run", "--rm", "--network", "none", "--entrypoint", "/bin/sh",
            *mounts, "-v", f"{root}:/restore", IMAGE, "-ec",
            "mv /data/db /restore/new-db-rollback; mkdir /data/db; "
            "rm -rf /var/lib/openldap; ln -s /data/db /var/lib/openldap; "
            "slapadd -F /data/config -n 1 -l /restore/old-directory.ldif; "
            "chown -R 389:389 /data/db", timeout=60)
        start()
        after = ldap("ldapsearch", "-LLL", "-o", "ldif-wrap=no", "-b", READER,
                     "-s", "base", "entryUUID", "userPassword").stdout
        assert sorted(before.splitlines()) == sorted(after.splitlines()), \
            "migration changed stable identity or stored password hash"
        identities_after = ldap("ldapsearch", "-LLL", "-o", "ldif-wrap=no",
                                "-b", SUFFIX, "(objectClass=*)", "entryUUID").stdout
        assert sorted(identities_before.splitlines()) == sorted(identities_after.splitlines()), \
            "migration changed an account or group UUID"
        verify()
        print("PASS: current pinned image -> Nfrastack preserves entryUUID, hashes, OTP, OAuth, groups",
              flush=True)
        run("docker", "rm", "-f", name)
        run("docker", "run", "--rm", "--network", "none", "--entrypoint", "/bin/sh",
            "-v", f"{root}:/prepare", IMAGE, "-ec",
            "mkdir -p /prepare/run/run; chown -R 389:389 /prepare/run")
        restricted_runtime = True
        start()
        verify()
        verify_renewal()
        denied = run("docker", "exec", "-u", "389:389", "-i", name,
                     "ldapmodify", "-Q", "-Y", "EXTERNAL", "-H", "ldapi:///", check=False,
                     data="dn: cn=config\nchangetype: modify\nreplace: olcSecurity\nolcSecurity: ssf=0\n")
        assert denied.returncode == 50, "TLS reloader can modify unrelated server settings"
        print("PASS: non-root read-only runtime, all capabilities dropped, scoped TLS reload", flush=True)
        run("docker", "rm", "-f", name)
        run("docker", "run", "--rm", "--network", "none", "--entrypoint", "/bin/sh",
            "-v", f"{root}:/prepare", IMAGE, "-ec",
            "mv /prepare/data /prepare/native-data-rollback; "
            "mkdir -p /prepare/data/config /prepare/data/db; chown -R 389:389 /prepare/data; "
            "chgrp 389 /prepare/secrets/admin /prepare/old-directory.ldif; "
            "chmod 640 /prepare/secrets/admin /prepare/old-directory.ldif")
        preparation = root / "preparation"
        preparation.mkdir()
        for filename in ("prepare.sh", "prepare.conf"):
            shutil.copyfile(HERE / filename, preparation / filename)
        shutil.copyfile(root / "schema/oauth.schema", preparation / "oauth.schema.conf")
        run("docker", "run", "--rm", "--network", "none", "--user", "389:389",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--read-only",
            "--entrypoint", "/bin/sh", "-v", f"{root / 'data'}:/data",
            "-v", f"{root / 'data/db'}:/var/lib/openldap", "-v", f"{root / 'run'}:/run/openldap",
            "-v", f"{root / 'certs'}:/certs:ro", "-v", f"{root / 'secrets'}:/secrets:ro",
            "-v", f"{preparation}:/source:ro",
            "-v", f"{root / 'old-directory.ldif'}:/restore/directory.ldif:ro",
            IMAGE, "/source/prepare.sh")
        start()
        verify()
        verify_renewal()
        identities_prepared = ldap("ldapsearch", "-LLL", "-o", "ldif-wrap=no",
                                  "-b", SUFFIX, "(objectClass=*)", "entryUUID").stdout
        assert sorted(identities_before.splitlines()) == sorted(identities_prepared.splitlines())
        print("PASS: one-time restricted preparation imports old data with identical account/group UUIDs",
              flush=True)
        verify_watcher()
        successful = True
    finally:
        run("docker", "rm", "-f", name + "-tls", check=False)
        run("docker", "rm", "-f", name, check=False)
        run("docker", "network", "rm", network, check=False)
        if successful and args.keep:
            print(f"Synthetic example retained: LDAP_TEST_DIR={root}")
        else:
            # The image owns test data as UID 389. Cleanup only this generated tree.
            run("docker", "run", "--rm", "--network", "none", "--entrypoint", "/bin/sh",
                "-v", f"{root}:/cleanup", IMAGE, "-ec", "rm -rf /cleanup/*", check=False)
            shutil.rmtree(root)


if __name__ == "__main__":
    main()
