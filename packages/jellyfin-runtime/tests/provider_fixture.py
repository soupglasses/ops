# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT

"""Synthetic fixture driver for the real generated Upjet/Framework connector."""

import json
import os
from pathlib import Path
import subprocess
import urllib.request


class ProviderFixture:
    def __init__(self, config_path, credentials_dir):
        self.config_path = Path(config_path)
        self.credentials_dir = Path(credentials_dir)
        self.token = None

    def request(self, method, path, body=None, authenticated=True):
        config = json.loads(self.config_path.read_text())
        headers = {
            "Content-Type": "application/json",
            "Authorization": 'MediaBrowser Client="Fixture", Device="Fixture", DeviceId="fixture", Version="1"',
        }
        if authenticated:
            headers["Authorization"] += ", Token=" + json.dumps(self.token)
        request = urllib.request.Request(
            config["baseURL"] + path,
            headers=headers,
            method=method,
            data=None if body is None else json.dumps(body).encode(),
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
        return json.loads(raw) if raw else None

    def run_once(self, check_only=False):
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "host",
                "--mount",
                f"type=bind,source={self.config_path},target=/desired.json,readonly",
                "--mount",
                f"type=bind,source={self.credentials_dir},target=/credentials,readonly",
                "--env",
                "JELLYFIN_TEST_CONFIG=/desired.json",
                "--env",
                "JELLYFIN_TEST_CREDENTIALS=/credentials",
                "--env",
                f"JELLYFIN_TEST_CHECK_ONLY={str(check_only).lower()}",
                os.environ["JELLYFIN_PROVIDER_TEST_IMAGE"],
                "-test.run=^TestLiveJellyfinSettings$",
                "-test.v",
            ],
            check=True,
        )
        self.authenticate()

    def authenticate(self):
        config = json.loads(self.config_path.read_text())
        result = self.request(
            "POST",
            "/Users/AuthenticateByName",
            {
                "Username": config["adminUsername"],
                "Pw": (self.credentials_dir / "JELLYFIN_ADMIN_PASSWORD").read_text(),
            },
            authenticated=False,
        )
        self.token = result["AccessToken"]
