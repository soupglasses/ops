# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT
"""Verify the deployed Grafana OAuth settings against a synthetic OAuth server."""

import base64
import configparser
import hashlib
from http.cookiejar import CookieJar
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
import re
from pathlib import Path
import socket
import subprocess
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

import yaml
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa


@unittest.skipUnless(
    os.getenv("GRAFANA_TEST_IMAGE"),
    "set GRAFANA_TEST_IMAGE for real Grafana OAuth tests",
)
class GrafanaOIDC(unittest.TestCase):
    def test_pictures_signed_tokens_group_access_pkce_and_local_recovery(self):
        values = yaml.safe_load(
            Path(
                "kubernetes/apps/monitoring/victoria-metrics/app/helmrelease.yaml"
            ).read_text()
        )["spec"]["values"]["grafana"]
        settings = values["grafana.ini"]
        provider = settings["auth.generic_oauth"]
        self.assertFalse(provider["auto_login"])
        self.assertFalse(settings["users"]["allow_sign_up"])
        state = {
            "member": True,
            "challenges": {},
            "exchanges": 0,
            "picture": "https://idp.example/photo/alice",
        }

        def navigation_avatar(response):
            match = re.search(
                r"user:\s*(\{.*?\}),\s*settings:", response.read().decode(), re.S
            )
            self.assertIsNotNone(match)
            return json.loads(match.group(1))["gravatarUrl"]

        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_key = private_key.public_key().public_numbers()

        def encoded(value):
            return base64.urlsafe_b64encode(value).decode().rstrip("=")

        jwk = {
            "kty": "RSA",
            "kid": "fixture",
            "use": "sig",
            "alg": "RS256",
            "n": encoded(public_key.n.to_bytes(256, "big")),
            "e": encoded(public_key.e.to_bytes(3, "big")),
        }

        def id_token(code):
            member = code in ("member", "tampered")
            claims = {
                "iss": issuer,
                "aud": "monitoring.grafana",
                "sub": code,
                "iat": int(time.time()),
                "exp": int(time.time()) + 3600,
                "email": code + "@example.invalid",
                "groups": (
                    ["monitoring.grafana-admins"] if member else ["unrelated-admins"]
                ),
            }
            if code == "missing":
                del claims["groups"]
            nonce = state.get("nonce", {}).get(code)
            if nonce:
                claims["nonce"] = nonce
            payload = (
                encoded(json.dumps({"alg": "RS256", "kid": "fixture"}).encode())
                + "."
                + encoded(json.dumps(claims).encode())
            )
            signature = private_key.sign(
                payload.encode(), padding.PKCS1v15(), hashes.SHA256()
            )
            if code == "tampered":
                signature = bytes([signature[0] ^ 1]) + signature[1:]
            return payload + "." + encoded(signature)

        class Identity(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                path = urllib.parse.urlsplit(self.path)
                if path.path == "/authorize":
                    query = urllib.parse.parse_qs(path.query)
                    assert query["code_challenge_method"] == ["S256"]
                    code = (
                        "tampered"
                        if state["member"] == "tampered"
                        else (
                            "missing"
                            if state["member"] is None
                            else "member" if state["member"] else "outsider"
                        )
                    )
                    state["challenges"][code] = query["code_challenge"][0]
                    state.setdefault("nonce", {})[code] = query.get("nonce", [None])[0]
                    location = (
                        query["redirect_uri"][0]
                        + "?"
                        + urllib.parse.urlencode(
                            {"code": code, "state": query["state"][0]}
                        )
                    )
                    self.send_response(302)
                    self.send_header("Location", location)
                    self.end_headers()
                elif path.path == "/jwks.json":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"keys": [jwk]}).encode())
                elif path.path == "/userinfo":
                    member = self.headers["Authorization"] == "Bearer member"
                    body = json.dumps(
                        {
                            "sub": "member" if member else "outsider",
                            "picture": state["picture"],
                            "name": "Fixture",
                            "email": (
                                "member@example.invalid"
                                if member
                                else "outsider@example.invalid"
                            ),
                            "groups": (
                                ["monitoring.grafana-admins"]
                                if member
                                else ["unrelated-admins"]
                            ),
                        }
                    ).encode()
                    if self.headers["Authorization"] == "Bearer missing":
                        profile = json.loads(body)
                        del profile["groups"]
                        body = json.dumps(profile).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_error(404)

            def do_POST(self):
                query = urllib.parse.parse_qs(
                    self.rfile.read(int(self.headers["Content-Length"])).decode()
                )
                assert query["client_id"] == ["monitoring.grafana"]
                assert query["client_secret"] == ["synthetic-oauth-secret"]
                assert not self.headers.get("Authorization")
                code = query["code"][0]
                challenge = (
                    base64.urlsafe_b64encode(
                        hashlib.sha256(query["code_verifier"][0].encode()).digest()
                    )
                    .decode()
                    .rstrip("=")
                )
                assert challenge == state["challenges"][code]
                state["exchanges"] += 1
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps(
                        {
                            "access_token": code,
                            "token_type": "Bearer",
                            "expires_in": 3600,
                            "id_token": id_token(code),
                        }
                    ).encode()
                )

        identity = ThreadingHTTPServer(("0.0.0.0", 0), Identity)
        self.addCleanup(identity.server_close)
        self.addCleanup(identity.shutdown)
        threading.Thread(target=identity.serve_forever, daemon=True).start()
        gateway = json.loads(
            subprocess.check_output(["docker", "network", "inspect", "bridge"])
        )[0]["IPAM"]["Config"][0]["Gateway"]
        issuer = f"http://{gateway}:{identity.server_port}"
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        settings["server"]["root_url"] = base
        settings["server"]["domain"] = "127.0.0.1"
        settings["security"]["cookie_secure"] = False
        provider.update(
            client_secret="synthetic-oauth-secret",
            auth_url=issuer + "/authorize",
            token_url=issuer + "/token",
            api_url=issuer + "/userinfo",
            jwk_set_url=issuer + "/jwks.json",
        )
        ini = configparser.ConfigParser(interpolation=None)
        ini.read_dict(
            {
                section: {
                    key: str(value).lower() if isinstance(value, bool) else str(value)
                    for key, value in config.items()
                }
                for section, config in settings.items()
            }
        )
        text = io.StringIO()
        ini.write(text)
        container = (
            subprocess.check_output(
                [
                    "docker",
                    "run",
                    "--detach",
                    "--publish",
                    f"127.0.0.1:{port}:3000",
                    "--env",
                    "GF_SECURITY_ADMIN_PASSWORD=synthetic-recovery-password",
                    "--env",
                    "GF_LOG_LEVEL=error",
                    os.environ["GRAFANA_TEST_IMAGE"],
                ]
            )
            .decode()
            .strip()
        )
        self.addCleanup(
            lambda: subprocess.run(
                ["docker", "rm", "--force", container],
                stdout=subprocess.DEVNULL,
                check=True,
            )
        )
        # Grafana reads this fixed filename at startup. Restart only this disposable fixture.
        subprocess.run(
            [
                "docker",
                "exec",
                "--user",
                "0",
                "-i",
                container,
                "/usr/share/grafana/bin/grafana",
                "--version",
            ],
            stdout=subprocess.DEVNULL,
            check=True,
        )
        temporary = Path("/tmp") / ("grafana-oidc-" + container[:12] + ".ini")
        temporary.write_text(text.getvalue())
        self.addCleanup(temporary.unlink)
        subprocess.run(
            ["docker", "cp", str(temporary), container + ":/etc/grafana/grafana.ini"],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        subprocess.run(
            ["docker", "restart", container], check=True, stdout=subprocess.DEVNULL
        )
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(CookieJar()),
        )
        for _ in range(480):
            try:
                with opener.open(base + "/api/health", timeout=2):
                    break
            except OSError:
                time.sleep(0.25)
        else:
            logs = subprocess.check_output(
                ["docker", "logs", container], stderr=subprocess.STDOUT
            ).decode()
            self.fail("Grafana fixture did not start: " + logs[-5000:])
        with opener.open(base + "/login") as response:
            self.assertEqual(urllib.parse.urlsplit(response.url).path, "/login")
        with opener.open(base + "/login/generic_oauth"):
            pass
        with opener.open(base + "/api/user") as response:
            profile = json.load(response)
            self.assertTrue(profile["isGrafanaAdmin"])
            self.assertEqual(profile["avatarUrl"], state["picture"])
        with opener.open(base + "/") as response:
            self.assertEqual(navigation_avatar(response), state["picture"])
        for picture in ("https://idp.example/photo/updated", ""):
            opener.open(base + "/logout").close()
            state["picture"] = picture
            with opener.open(base + "/login/generic_oauth"):
                pass
            expected = picture or "/public/img/user_profile.png"
            with opener.open(base + "/api/user") as response:
                self.assertEqual(json.load(response)["avatarUrl"], expected)
            with opener.open(base + "/") as response:
                self.assertEqual(navigation_avatar(response), expected)
        opener.open(base + "/logout").close()
        for membership in (False, None, "tampered"):
            state["member"] = membership
            with opener.open(base + "/login/generic_oauth"):
                pass
            with self.assertRaises(urllib.error.HTTPError) as denied:
                opener.open(base + "/api/user")
            self.assertEqual(denied.exception.code, 401)
            denied.exception.close()
        self.assertEqual(state["exchanges"], 6)
        body = json.dumps(
            {"user": "admin", "password": "synthetic-recovery-password"}
        ).encode()
        with opener.open(
            urllib.request.Request(
                base + "/login", data=body, headers={"Content-Type": "application/json"}
            )
        ) as response:
            self.assertEqual(response.status, 200)
        opener.open(base + "/logout").close()


if __name__ == "__main__":
    unittest.main()
