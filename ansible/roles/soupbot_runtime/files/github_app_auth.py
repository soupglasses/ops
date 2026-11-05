#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: 0BSD

"""Mint a soupbot-ai installation token on the trusted credential host.

Requires Python 3 and OpenSSL. Writes only the token to stdout; does not cache it.
Keep this helper and the app private key on the credential host, not in a sandbox.
"""

import argparse
import base64
import json
from pathlib import Path
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request


def encode(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=")


def jwt(client_id, key):
    now = int(time.time())
    header = encode(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = encode(json.dumps({"iat": now - 60, "exp": now + 540, "iss": client_id}).encode())
    payload = header + b"." + claims
    signature = subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", str(key)],
        input=payload, capture_output=True, check=True,
    ).stdout
    return (payload + b"." + encode(signature)).decode()


def request(path, token, method="GET"):
    req = urllib.request.Request(
        "https://api.github.com" + path,
        data=b"{}" if method == "POST" else None,
        method=method,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "soupbot-ai-installation-auth",
            "X-GitHub-Api-Version": "2026-03-10",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--key-file", required=True, type=Path)
    parser.add_argument("--installation-id", default=158330008, type=int)
    args = parser.parse_args()
    info = args.key_file.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise ValueError("Private key must be a regular file with permissions 0600 or 0400")
    token = jwt(args.client_id, args.key_file)
    path = f"/app/installations/{args.installation_id}"
    installation = request(path, token)
    if installation.get("app_slug") != "soupbot-ai" or installation["account"]["login"] != "soupglasses":
        raise ValueError("Expected the soupbot-ai installation on soupglasses")
    result = request(path + "/access_tokens", token, "POST")
    if not result.get("token", "").startswith("ghs_"):
        raise ValueError("GitHub did not return an installation token")
    print(result["token"])


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as error:
        sys.exit(f"GitHub authentication failed: HTTP {error.code}")
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, urllib.error.URLError):
        sys.exit("Could not issue installation token; check client ID, private key, installation and connectivity")
