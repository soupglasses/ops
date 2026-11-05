# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: 0BSD

import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from github_app_broker import Handler, Server, Sodium, TokenSource


class Fixture:
    def respond(self, request):
        if request.get("account") != "github" and request.get("issuer") != "https://github.com":
            raise ValueError("Unknown account")
        return {"status": "success", "access_token": "fixture-only-token",
                "issuer": "https://github.com", "expires_at": 2000000000}


class BrokerTests(unittest.TestCase):
    def test_real_oidc_token_encrypted_protocol(self):
        with tempfile.TemporaryDirectory() as directory:
            address = str(Path(directory) / "agent.sock")
            with Server(address, Handler) as server:
                server.sodium, server.source = Sodium(), Fixture()
                thread = threading.Thread(target=server.serve_forever)
                thread.start()
                try:
                    env = dict(os.environ, OIDC_SOCK=address)
                    for args, expected in [
                        (["github", "--time=300", "--name=test"], "fixture-only-token"),
                        (["github", "--force-new"], "fixture-only-token"),
                        (["github", "--issuer"], "https://github.com"),
                        (["github", "--expires-at"], "2000000000"),
                        (["https://github.com"], "fixture-only-token"),
                    ]:
                        result = subprocess.run(["/usr/bin/oidc-token", *args], env=env,
                                                capture_output=True, text=True, timeout=10)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(result.stdout.strip(), expected)
                    result = subprocess.run(["/usr/bin/oidc-token", "other-account"], env=env,
                                            capture_output=True, text=True, timeout=10)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertNotIn("fixture-only-token", result.stdout + result.stderr)
                    # An invalid handshake must not stop the next valid request.
                    with socket.socket(socket.AF_UNIX) as client:
                        client.connect(address)
                        client.sendall(b"!" * 44)
                    result = subprocess.run(["/usr/bin/oidc-token", "github"], env=env,
                                            capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.stdout.strip(), "fixture-only-token")
                finally:
                    server.shutdown()
                    thread.join()

    @patch("github_app_broker.auth.jwt", return_value="fixture-jwt")
    def test_cache_refresh_and_scope_rejection(self, signer):
        calls = []

        def request(path, token, method="GET"):
            calls.append((path, method))
            self.assertEqual(token, "fixture-jwt")
            if method == "GET":
                return {"app_slug": "soupbot-ai", "account": {"login": "soupglasses"}}
            return {"token": "ghs_fixture-only", "expires_at": "2099-01-01T00:00:00Z"}

        source = TokenSource("client", Path("unused"), 158330008, request)
        query = {"request": "access_token", "account": "github", "min_valid_period": "300"}
        self.assertEqual(source.respond(query)["access_token"], "ghs_fixture-only")
        source.respond(query)
        self.assertEqual(len(calls), 2)
        source.cached["expires_at"] = int(time.time()) + 20
        source.respond(query)
        self.assertEqual(len(calls), 4)
        source.respond(dict(query, min_valid_period=-1))
        self.assertEqual(len(calls), 6)
        for extra in [{"scope": "repo"}, {"audience": "elsewhere"},
                      {"min_valid_period": 3601}, {"account": "soupglasses"}]:
            with self.assertRaises(ValueError):
                source.respond(dict(query, **extra))
        self.assertEqual(len(calls), 6)

    @patch("github_app_broker.auth.jwt", return_value="fixture-jwt")
    def test_wrong_installation_and_failure_never_fall_back(self, signer):
        source = TokenSource("client", Path("unused"), 158330008,
                             lambda *args: {"app_slug": "soupbot-ai", "account": {"login": "soupbot"}})
        with self.assertRaises(ValueError):
            source.respond({"request": "access_token", "account": "github"})
        self.assertIsNone(source.cached)


if __name__ == "__main__":
    unittest.main()
