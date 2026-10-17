# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT
"""Reconcile owned Jellyfin API fields without persisting response bodies or tokens."""

import argparse
import copy
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


class ConfigurationError(Exception):
    """A deliberately body-free operational error."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def merge(current, desired):
    result = copy.deepcopy(current)
    for key, value in desired.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def contains(current, desired):
    if isinstance(desired, dict):
        return isinstance(current, dict) and all(
            key in current and contains(current[key], value)
            for key, value in desired.items()
        )
    return type(current) is type(desired) and current == desired


class Reconciler:
    def __init__(self, config_path, credentials_dir):
        self.config_path = Path(config_path)
        self.credentials_dir = Path(credentials_dir)
        self.token = None
        self.opener = urllib.request.build_opener(NoRedirect())

    def credential(self, name):
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or name in ("", ".", "..")
        ):
            raise ConfigurationError("invalid credential reference")
        try:
            value = (self.credentials_dir / name).read_text()
        except (OSError, UnicodeError):
            raise ConfigurationError("credential file unavailable") from None
        if not value:
            raise ConfigurationError("empty credential file")
        return value

    def request(self, method, path, body=None, authenticated=True, retry=True):
        if not path.startswith("/") or path.startswith("//"):
            raise ConfigurationError("invalid API path")
        headers = {"Content-Type": "application/json"}
        if authenticated and self.token:
            headers["X-Emby-Token"] = self.token
        headers["Authorization"] = (
            'MediaBrowser Client="ops-configuration", Device="Kubernetes", '
            'DeviceId="ops-configuration", Version="1"'
        )
        data = json.dumps(body).encode() if body is not None else None
        try:
            with self.opener.open(
                urllib.request.Request(
                    self.base_url + path, data=data, headers=headers, method=method
                ),
                timeout=15,
            ) as response:
                content = response.read()
                return json.loads(content) if content else None
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            if status == 401 and authenticated and retry:
                self.token = None
                self.authenticate()
                return self.request(method, path, body, authenticated, retry=False)
            raise ConfigurationError(f"API request failed (HTTP {status})") from None
        except (OSError, ValueError):
            raise ConfigurationError("API request failed") from None

    def authenticate(self):
        result = self.request(
            "POST",
            "/Users/AuthenticateByName",
            {
                "Username": self.config["adminUsername"],
                "Pw": self.credential("JELLYFIN_ADMIN_PASSWORD"),
            },
            authenticated=False,
        )
        self.token = result.get("AccessToken") if isinstance(result, dict) else None
        if not self.token:
            raise ConfigurationError("administrator authentication failed")

    def load(self):
        try:
            self.config = json.loads(self.config_path.read_text())
            self.base_url = self.config["baseURL"].rstrip("/")
            parsed = urllib.parse.urlsplit(self.base_url)
            if parsed.scheme != "http" or parsed.hostname not in (
                "127.0.0.1",
                "localhost",
                "::1",
            ):
                raise ConfigurationError("configuration API must use pod loopback HTTP")
            if (
                parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or parsed.path
            ):
                raise ConfigurationError("invalid configuration API URL")
            self.config["adminUsername"]
            self.config["resources"]
        except (OSError, ValueError, KeyError, TypeError):
            raise ConfigurationError("invalid desired configuration") from None

    def desired(self, resource):
        result = copy.deepcopy(resource["values"])
        for secret in resource.get("secretValues", []):
            path = secret["path"]
            if not path or any(not isinstance(key, str) or not key for key in path):
                raise ConfigurationError("invalid secret field path")
            parent = result
            for key in path[:-1]:
                parent = parent.setdefault(key, {})
            parent[path[-1]] = self.credential(secret["file"])
        return result

    def reconcile_libraries(self, check_only):
        desired = self.config.get("libraries", [])
        if not desired:
            return
        observed = self.request("GET", "/Library/VirtualFolders")
        if not isinstance(observed, list):
            raise ConfigurationError("library endpoint returned an invalid document")
        for library in desired:
            name, kind, paths = (
                library["name"],
                library["collectionType"],
                library["paths"],
            )
            if not paths or any(
                not path.startswith("/media/") or ".." in Path(path).parts
                for path in paths
            ):
                raise ConfigurationError("library paths must be within the media mount")
            matches = [item for item in observed if item.get("Name") == name]
            if not matches:
                if check_only:
                    raise ConfigurationError("managed media library is missing")
                query = urllib.parse.urlencode(
                    {"name": name, "collectionType": kind, "refreshLibrary": "false"}
                )
                self.request(
                    "POST",
                    "/Library/VirtualFolders?" + query,
                    {
                        "LibraryOptions": {
                            "PathInfos": [{"Path": path} for path in paths]
                        }
                    },
                )
                continue
            if len(matches) != 1 or matches[0].get("CollectionType") != kind:
                raise ConfigurationError(
                    "managed media library has a conflicting collection type"
                )
            for path in paths:
                if path not in matches[0].get("Locations", []):
                    if check_only:
                        raise ConfigurationError(
                            "managed media library path is missing"
                        )
                    self.request(
                        "POST",
                        "/Library/VirtualFolders/Paths?refreshLibrary=false",
                        {"Name": name, "Path": path},
                    )

    def run_once(self, check_only=False):
        self.load()
        info = self.request("GET", "/System/Info/Public", authenticated=False)
        if not info.get(
            "StartupWizardCompleted", info.get("IsStartupWizardCompleted", False)
        ):
            if check_only:
                raise ConfigurationError("startup wizard is incomplete")
            self.request(
                "POST",
                "/Startup/Configuration",
                self.config["startup"]["configuration"],
                False,
            )
            self.request("GET", "/Startup/User", authenticated=False)
            self.request(
                "POST",
                "/Startup/User",
                {
                    "Name": self.config["adminUsername"],
                    "Password": self.credential("JELLYFIN_ADMIN_PASSWORD"),
                },
                False,
            )
            self.request(
                "POST",
                "/Startup/RemoteAccess",
                self.config["startup"]["remoteAccess"],
                False,
            )
            self.request("POST", "/Startup/Complete", authenticated=False)
        # Reuse sessions to avoid growing Jellyfin's session database every poll.
        if not self.token:
            self.authenticate()
        for resource in self.config["resources"]:
            desired = self.desired(resource)
            current = self.request("GET", resource["path"])
            if not isinstance(current, dict):
                raise ConfigurationError(
                    "configuration endpoint returned an invalid document"
                )
            if not contains(current, desired):
                if check_only:
                    raise ConfigurationError("managed configuration has drifted")
                self.request("POST", resource["path"], merge(current, desired))
        self.reconcile_libraries(check_only)
        return int(self.config.get("intervalSeconds", 60))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/desired/configuration.json")
    parser.add_argument("--credentials", default="/credentials")
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify owned fields without changing server settings",
    )
    parser.add_argument(
        "--health", action="store_true", help="check recent successful reconciliation"
    )
    parser.add_argument(
        "--health-file", type=Path, default=Path("/run/jellyfin/configuration.ready")
    )
    args = parser.parse_args()
    if args.health:
        try:
            return 0 if time.time() - args.health_file.stat().st_mtime < 180 else 1
        except OSError:
            return 1
    reconciler = Reconciler(args.config, args.credentials)
    while True:
        interval = 30
        try:
            interval = reconciler.run_once(check_only=args.check)
            if not args.once and not args.check:
                args.health_file.parent.mkdir(parents=True, exist_ok=True)
                args.health_file.touch()
            print("Jellyfin configuration reconciled", flush=True)
        except ConfigurationError as error:
            if not args.once and not args.check:
                args.health_file.unlink(missing_ok=True)
            print(str(error), file=sys.stderr, flush=True)
            if args.once or args.check:
                return 1
        if args.once or args.check:
            return 0
        time.sleep(max(5, interval))


if __name__ == "__main__":
    sys.exit(main())
