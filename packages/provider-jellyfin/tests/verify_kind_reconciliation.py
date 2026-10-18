# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT
"""Exercise the installed controller against a disposable Jellyfin in kind."""

import json
import socket
import subprocess
import time
import urllib.error
import urllib.request


def kube(*args, value=None):
    return subprocess.check_output(
        ["kubectl", "-n", "media", *args],
        input=None if value is None else json.dumps(value).encode(),
    )


def apply(value):
    kube("apply", "-f", "-", value=value)


def eventually(check, description):
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        try:
            if check():
                return
        except (urllib.error.URLError, ConnectionError):
            pass
        time.sleep(2)
    raise AssertionError(description)


apply(
    {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": "jellyfin-fixture", "labels": {"app": "jellyfin-fixture"}},
        "spec": {
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 1000,
                "runAsGroup": 1000,
                "fsGroup": 1000,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [
                {
                    "name": "jellyfin",
                    "image": "jellyfin/jellyfin:12.2@sha256:357724bf0ae27a672c7cbaa899db2d9abeb13dbd8657ccce750258a4c059d037",
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "capabilities": {"drop": ["ALL"]},
                    },
                    "volumeMounts": [
                        {"name": name, "mountPath": "/" + name}
                        for name in ("config", "cache")
                    ],
                }
            ],
            "volumes": [{"name": name, "emptyDir": {}} for name in ("config", "cache")],
        },
    }
)
apply(
    {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": "jellyfin-fixture"},
        "spec": {
            "selector": {"app": "jellyfin-fixture"},
            "ports": [{"port": 8096, "targetPort": 8096}],
        },
    }
)
kube("wait", "pod/jellyfin-fixture", "--for=condition=Ready", "--timeout=180s")
with socket.socket() as listener:
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
forward = subprocess.Popen(
    [
        "kubectl",
        "-n",
        "media",
        "port-forward",
        "service/jellyfin-fixture",
        f"{port}:8096",
    ],
    stdout=subprocess.DEVNULL,
)
token = None


def api(path, value=None):
    headers = {
        "Content-Type": "application/json",
        "Authorization": 'MediaBrowser Client="KindTest", Device="Fixture", DeviceId="kind-test", Version="1"',
    }
    if token:
        headers["Authorization"] += ", Token=" + json.dumps(token)
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=None if value is None else json.dumps(value).encode(),
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        raw = response.read()
        return (
            json.loads(raw)
            if raw and response.headers.get_content_type() == "application/json"
            else raw
        )


try:
    eventually(lambda: api("/health") == b"Healthy", "Jellyfin did not start")
    password = "disposable-kind-test-password"
    api(
        "/Startup/Configuration",
        {
            "UICulture": "en-US",
            "MetadataCountryCode": "US",
            "PreferredMetadataLanguage": "en",
        },
    )
    # Jellyfin creates its first administrator when the setup wizard reads this endpoint.
    api("/Startup/User")
    api("/Startup/User", {"Name": "ops-recovery", "Password": password})
    api(
        "/Startup/RemoteAccess",
        {"EnableRemoteAccess": True, "EnableAutomaticPortMapping": False},
    )
    api("/Startup/Complete", {})
    token = api(
        "/Users/AuthenticateByName", {"Username": "ops-recovery", "Pw": password}
    )["AccessToken"]
    users = api("/Users")
    baseline = api("/System/Configuration")
    apply(
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": "kind-credentials"},
            "stringData": {"password": password},
        }
    )
    apply(
        {
            "apiVersion": "jellyfin.ops.finnes.dev/v1beta1",
            "kind": "ProviderConfig",
            "metadata": {"name": "kind-fixture"},
            "spec": {
                "endpoint": "http://jellyfin-fixture.media.svc.cluster.local:8096",
                "username": "ops-recovery",
                "passwordSecretRef": {"name": "kind-credentials", "key": "password"},
            },
        }
    )
    resource = {
        "apiVersion": "jellyfin.ops.finnes.dev/v1alpha1",
        "kind": "SystemConfiguration",
        "metadata": {
            "name": "kind-fixture",
            "annotations": {"crossplane.io/external-name": "system"},
        },
        "spec": {
            "managementPolicies": ["Observe"],
            "providerConfigRef": {"name": "kind-fixture", "kind": "ProviderConfig"},
            "forProvider": {"serverName": "Managed by Crossplane"},
        },
    }
    apply(resource)
    kube(
        "wait",
        "systemconfiguration/kind-fixture",
        "--for=condition=Synced",
        "--timeout=120s",
    )
    assert api("/System/Configuration")["ServerName"] == baseline["ServerName"], (
        "Observe wrote settings"
    )
    resource["spec"]["managementPolicies"] = ["Observe", "Create", "Update"]
    apply(resource)
    eventually(
        lambda: api("/System/Configuration")["ServerName"] == "Managed by Crossplane",
        "controller did not apply desired settings",
    )
    drift = api("/System/Configuration")
    drift["ServerName"] = "External drift"
    api("/System/Configuration", drift)
    eventually(
        lambda: api("/System/Configuration")["ServerName"] == "Managed by Crossplane",
        "controller did not correct API drift",
    )
    kube(
        "wait",
        "systemconfiguration/kind-fixture",
        "--for=condition=Synced",
        "--timeout=120s",
    )
    for field in ("UICulture", "MetadataCountryCode", "PreferredMetadataLanguage"):
        assert api("/System/Configuration")[field] == baseline[field], field
    assert [(u["Id"], u["Name"]) for u in api("/Users")] == [
        (u["Id"], u["Name"]) for u in users
    ]
    print(
        "Kind controller adopted settings without writes, applied desired state, corrected drift, and preserved users."
    )
finally:
    forward.terminate()
    forward.wait(timeout=10)
