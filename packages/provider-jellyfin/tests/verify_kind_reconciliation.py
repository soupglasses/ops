# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT
"""Exercise the installed controller against a disposable Jellyfin in kind."""

import json
import os
from pathlib import Path
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
                    "image": os.environ.get("JELLYFIN_TEST_IMAGE") or next(
                        line.split()[1]
                        for line in (
                            Path(__file__).resolve().parents[2]
                            / "jellyfin-runtime/Dockerfile"
                        )
                        .read_text()
                        .splitlines()
                        if line.startswith("FROM jellyfin/jellyfin:")
                    ),
                    "readinessProbe": {
                        "httpGet": {"path": "/health", "port": 8096},
                        "periodSeconds": 2,
                        "timeoutSeconds": 2,
                    },
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


def start_forward():
    return subprocess.Popen(
        [
            "kubectl",
            "-n",
            "media",
            "port-forward",
            "pod/jellyfin-fixture",
            f"{port}:8096",
        ],
        stdout=subprocess.DEVNULL,
    )


forward = start_forward()
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


def fixture_ready():
    global forward
    # Jellyfin can restart its listener during startup, which terminates port-forward.
    if forward.poll() is not None:
        forward = start_forward()
    return api("/health") == b"Healthy"


try:
    eventually(fixture_ready, "Jellyfin did not start")
    password = "disposable-kind-test-password"
    initial = api("/System/Info/Public")
    assert initial["StartupWizardCompleted"] is False, "fixture was already initialized"
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
            "managementPolicies": ["Observe", "Create", "Update"],
            "providerConfigRef": {"name": "kind-fixture", "kind": "ProviderConfig"},
            "forProvider": {"serverName": "Fresh Crossplane instance"},
        },
    }
    apply(resource)
    kube(
        "wait",
        "systemconfiguration/kind-fixture",
        "--for=condition=Synced",
        "--timeout=120s",
    )
    assert api("/System/Info/Public")["StartupWizardCompleted"] is True
    token = api(
        "/Users/AuthenticateByName", {"Username": "ops-recovery", "Pw": password}
    )["AccessToken"]
    users = api("/Users")
    assert len(users) == 1 and users[0]["Policy"]["IsAdministrator"], (
        "provider did not create exactly one first administrator"
    )
    assert api("/System/Info/Public")["Id"] == initial["Id"], "server was replaced"
    baseline = api("/System/Configuration")
    assert baseline["ServerName"] == "Fresh Crossplane instance"
    resource["spec"]["managementPolicies"] = ["Observe"]
    resource["spec"]["forProvider"]["serverName"] = "Managed by Crossplane"
    apply(resource)
    # Wait for a new reconciliation rather than accepting the previous Synced condition.
    generation = json.loads(
        kube("get", "systemconfiguration/kind-fixture", "-o", "json")
    )["metadata"]["generation"]
    eventually(
        lambda: any(
            condition["type"] == "Synced"
            and condition["status"] == "True"
            and condition.get("observedGeneration") == generation
            for condition in json.loads(
                kube("get", "systemconfiguration/kind-fixture", "-o", "json")
            )["status"]["conditions"]
        ),
        "Observe did not reconcile the new generation",
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
    plugin = next(item for item in api("/Plugins") if item["Name"] == "TMDb")
    plugin_path = f"/Plugins/{plugin['Id']}/Configuration"
    plugin_baseline = api(plugin_path)
    apply(
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": "kind-plugin-settings"},
            "stringData": {"configuration.json": "{}"},
        }
    )
    apply(
        {
            "apiVersion": "jellyfin.ops.finnes.dev/v1alpha1",
            "kind": "PluginConfiguration",
            "metadata": {
                "name": "kind-plugin",
                "annotations": {"crossplane.io/external-name": plugin["Id"]},
            },
            "spec": {
                "managementPolicies": ["Observe"],
                "providerConfigRef": {"name": "kind-fixture", "kind": "ProviderConfig"},
                "forProvider": {
                    "configurationJsonSecretRef": {
                        "name": "kind-plugin-settings",
                        "key": "configuration.json",
                    }
                },
            },
        }
    )
    for condition in ("Ready", "Synced"):
        kube(
            "wait",
            "pluginconfiguration/kind-plugin",
            f"--for=condition={condition}",
            "--timeout=120s",
        )
    assert api(plugin_path) == plugin_baseline, "Observe changed plugin configuration"
    assert [(u["Id"], u["Name"]) for u in api("/Users")] == [
        (u["Id"], u["Name"]) for u in users
    ]
    print(
        "Kind controller bootstrapped an empty server, adopted settings without writes, applied desired state, corrected drift, and preserved users."
    )
finally:
    forward.terminate()
    forward.wait(timeout=10)
