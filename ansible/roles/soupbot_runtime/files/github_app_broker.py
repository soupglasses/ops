#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: 0BSD

"""GitHub App installation tokens through stock oidc-token's encrypted IPC."""

import argparse
import base64
import ctypes as C
import ctypes.util
from datetime import datetime
import grp
import json
import os
from pathlib import Path
import socket
import socketserver
import threading
import time

import github_app_auth as auth


class Sodium:
    def __init__(self):
        self.lib = C.CDLL(ctypes.util.find_library("sodium"))
        if self.lib.sodium_init() < 0:
            raise RuntimeError("Cannot initialize IPC encryption")
        self.lib.crypto_box_keypair.argtypes = [C.c_void_p, C.c_void_p]
        self.lib.crypto_box_beforenm.argtypes = [C.c_void_p, C.c_char_p, C.c_void_p]
        args = [C.c_void_p, C.c_char_p, C.c_ulonglong, C.c_char_p, C.c_char_p]
        self.lib.crypto_secretbox_easy.argtypes = args
        self.lib.crypto_secretbox_open_easy.argtypes = args

    def handshake(self, client):
        peer = base64.b64decode(read_exact(client, 44), validate=True)
        if len(peer) != 32:
            raise ValueError("Invalid peer key")
        public, private, shared = (C.create_string_buffer(32) for _ in range(3))
        if self.lib.crypto_box_keypair(public, private) != 0:
            raise ValueError("Cannot generate IPC key")
        if self.lib.crypto_box_beforenm(shared, peer, private) != 0:
            raise ValueError("Invalid peer key")
        client.sendall(base64.b64encode(public.raw))
        return shared.raw

    def receive(self, client, key):
        length = bytearray()
        while True:
            value = read_exact(client, 1)
            if value == b":":
                break
            if len(length) >= 6 or value not in b"0123456789":
                raise ValueError("Invalid frame length")
            length.extend(value)
        size = int(length)
        if not 0 < size <= 65536:
            raise ValueError("IPC request too large")
        nonce = base64.b64decode(read_exact(client, 32), validate=True)
        if read_exact(client, 1) != b":":
            raise ValueError("Invalid encrypted frame")
        encrypted = base64.b64decode(read_exact(client, ((size + 18) // 3) * 4), validate=True)
        if len(nonce) != 24 or len(encrypted) != size + 16:
            raise ValueError("Invalid encrypted frame size")
        plain = C.create_string_buffer(size)
        if self.lib.crypto_secretbox_open_easy(plain, encrypted, len(encrypted), nonce, key) != 0:
            raise ValueError("IPC authentication failed")
        return json.loads(plain.raw)

    def send(self, client, key, value):
        plain = json.dumps(value, separators=(",", ":")).encode()
        nonce = os.urandom(24)
        encrypted = C.create_string_buffer(len(plain) + 16)
        if self.lib.crypto_secretbox_easy(encrypted, plain, len(plain), nonce, key) != 0:
            raise ValueError("IPC encryption failed")
        client.sendall(str(len(plain)).encode() + b":" + base64.b64encode(nonce)
                       + b":" + base64.b64encode(encrypted.raw))


def read_exact(client, size):
    result = bytearray()
    while len(result) < size:
        chunk = client.recv(size - len(result))
        if not chunk:
            raise ValueError("Client disconnected")
        result.extend(chunk)
    return bytes(result)


class TokenSource:
    def __init__(self, client_id, key_file, installation_id, requester=auth.request):
        self.client_id, self.key_file = client_id, key_file
        self.installation_id, self.requester = installation_id, requester
        self.cached = None
        self.lock = threading.Lock()

    def mint(self):
        jwt = auth.jwt(self.client_id, self.key_file)
        path = f"/app/installations/{self.installation_id}"
        installation = self.requester(path, jwt)
        if installation.get("app_slug") != "soupbot-ai" or installation["account"]["login"] != "soupglasses":
            raise ValueError("Wrong app installation")
        result = self.requester(path + "/access_tokens", jwt, "POST")
        if not result["token"].startswith("ghs_"):
            raise ValueError("Expected installation token")
        expiry = int(datetime.fromisoformat(result["expires_at"].replace("Z", "+00:00")).timestamp())
        return {"status": "success", "access_token": result["token"],
                "issuer": "https://github.com", "expires_at": expiry}

    def respond(self, request):
        if request.get("request") == "loaded_accounts":
            return {"status": "success", "info": ["github"]}
        if request.get("request") != "access_token":
            raise ValueError("Only installation access tokens are supported")
        account, issuer = request.get("account"), request.get("issuer")
        if not ((account == "github" and not issuer) or
                (not account and issuer in ("https://github.com", "https://github.com/"))):
            raise ValueError("Unknown account or issuer")
        if request.get("scope") or request.get("audience"):
            raise ValueError("OAuth scopes and audiences do not apply to installation tokens")
        period = request.get("min_valid_period", 0)
        if isinstance(period, str) and (period.isdecimal() or period == "-1"):
            period = int(period)
        if type(period) is not int or not -1 <= period <= 3500:
            raise ValueError("Validity must be between 0 and 3500 seconds, or -1 to refresh")
        force = period == -1
        period = max(0, period)
        with self.lock:
            if force or not self.cached or self.cached["expires_at"] <= time.time() + max(period, 300):
                self.cached = self.mint()
            if self.cached["expires_at"] <= time.time() + period:
                raise ValueError("Issued token has insufficient validity")
            return self.cached


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(35)
        try:
            key = self.server.sodium.handshake(self.request)
            request = self.server.sodium.receive(self.request, key)
        except Exception:
            return
        try:
            response = self.server.source.respond(request)
        except ValueError as error:
            response = {"status": "failure", "error": str(error)}
        except Exception:
            # Never include HTTP bodies, credentials or raw request data in logs.
            response = {"status": "failure", "error": "GitHub installation authentication unavailable"}
        try:
            self.server.sodium.send(self.request, key, response)
        except (OSError, ValueError):
            pass


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--group", default="oidc-shell")
    parser.add_argument("--client-id", default="Iv23liN1XrXce0vGIwUI")
    parser.add_argument("--installation-id", default=158330008, type=int)
    parser.add_argument("--key-file", required=True, type=Path)
    args = parser.parse_args()
    # Refuse to replace an existing socket or symlink; switching is explicit.
    os.umask(0o077)
    with Server(args.socket, Handler) as server:
        os.chown(args.socket, -1, grp.getgrnam(args.group).gr_gid)
        os.chmod(args.socket, 0o660)
        server.sodium = Sodium()
        server.source = TokenSource(args.client_id, args.key_file, args.installation_id)
        # Fail startup before accepting clients if the configured credential is unusable.
        server.source.cached = server.source.mint()
        server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
