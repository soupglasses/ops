# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT

"""Render the real composition with synthetic credentials, including JSON escapes."""

import base64
import json
from pathlib import Path
import subprocess
import tempfile


root = Path(__file__).resolve().parents[3]
composition = root / "kubernetes/apps/media/jellyfin/provider/composition.yaml"
xr = json.loads(
    subprocess.check_output(
        [
            "yq",
            "-o=json",
            ".",
            str(root / "kubernetes/apps/media/jellyfin/settings/ldap.yaml"),
        ]
    )
)
xr["metadata"]["namespace"] = "media"
xr["metadata"]["uid"] = "synthetic-composition"
password = 'synthetic "quoted" \\\npassword'
secret = {
    "apiVersion": "v1",
    "kind": "Secret",
    "metadata": {"name": "jellyfin-identity", "namespace": "media"},
    "data": {"LDAP_BIND_PASSWORD": base64.b64encode(password.encode()).decode()},
}
function = {
    "apiVersion": "pkg.crossplane.io/v1",
    "kind": "Function",
    "metadata": {"name": "function-go-templating"},
    "spec": {
        "package": "xpkg.crossplane.io/crossplane-contrib/function-go-templating:v0.13.1"
    },
}

with tempfile.TemporaryDirectory(prefix="jellyfin-composition-") as directory:
    work = Path(directory)
    for name, content in (("xr", xr), ("secret", secret), ("function", function)):
        (work / f"{name}.json").write_text(json.dumps(content))
    command = [
        "crossplane",
        "render",
        str(work / "xr.json"),
        str(composition),
        str(work / "function.json"),
        "--required-resources",
        str(work / "secret.json"),
        "--crossplane-version=v2.4.2",
    ]
    rendered = subprocess.check_output(command)
    documents = subprocess.check_output(
        ["yq", "eval-all", "-o=json", "-I=0", ".", "-"], input=rendered
    )
    objects = [
        json.loads(line) for line in documents.decode().splitlines() if line.strip()
    ]
    settings = next(item for item in objects if item["kind"] == "Secret")
    managed = next(item for item in objects if item["kind"] == "PluginConfiguration")
    payload = json.loads(base64.b64decode(settings["data"]["configuration.json"]))
    assert payload["LdapBindPassword"] == password
    assert payload["LdapServer"] == xr["spec"]["configuration"]["LdapServer"]
    assert (
        settings["metadata"]["namespace"] == managed["metadata"]["namespace"] == "media"
    )
    assert managed["spec"]["managementPolicies"] == xr["spec"]["managementPolicies"]
    assert (
        managed["spec"]["forProvider"]["configurationJsonSecretRef"]["name"]
        == settings["metadata"]["name"]
    )
    assert (
        managed["metadata"]["annotations"]["crossplane.io/external-name"]
        == xr["spec"]["pluginID"]
    )
    secret["data"] = {}
    (work / "secret.json").write_text(json.dumps(secret))
    failed = subprocess.run(command, capture_output=True)
    assert failed.returncode != 0
    assert b"Secret key is missing or empty" in failed.stderr

print(
    "PluginSettings composition preserves JSON credentials, namespace scope and declared management policies"
)
