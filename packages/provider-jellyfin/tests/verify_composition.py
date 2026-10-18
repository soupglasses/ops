# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT

"""Render credential publication, rotation and pending-state preservation."""

import base64
import copy
import json
from pathlib import Path
import subprocess
import tempfile


root = Path(__file__).resolve().parents[3]
composition = root / "kubernetes/apps/crossplane-system/crossplane/providers/jellyfin/composition.yaml"


def read_yaml(path, expression="[.]"):
    return json.loads(subprocess.check_output(["yq", "eval-all", "-o=json", expression, str(path)]))


xr = next(item for item in read_yaml(root / "kubernetes/apps/media/jellyfin/app/settings.yaml")
          if item["kind"] == "PluginSettings")
schema = read_yaml(composition.parent / "pluginsettings.yaml")[0]
for field, definition in schema["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["properties"]["spec"]["properties"].items():
    # CLI rendering does not apply admission defaults.
    if "default" in definition:
        xr["spec"].setdefault(field, definition["default"])
xr["metadata"].update(namespace="media", uid="synthetic-composition")
function = next(item for item in read_yaml(root / "kubernetes/apps/identity/ldap-provider/app/function.yaml")
                if item["kind"] == "Function")
field = "LdapBindPassword"
reference = xr["spec"]["secretValues"][field]
password = 'synthetic "quoted" \\\npassword'
conditions = [{"type": kind, "status": "True"} for kind in ("Ready", "Synced")]


def identity(version="7", ready=True):
    return {
        "apiVersion": "ops.finnes.dev/v1alpha1", "kind": "ServiceAccount",
        "metadata": {"name": reference["serviceAccountRef"]["name"], "namespace": "media"},
        "status": {
            "secretRef": {"name": "generated-bind-password", "resourceVersion": version},
            "conditions": [{"type": "Ready", "status": "True" if ready else "False"}, conditions[1]],
        },
    }


def secret(version="7", value=password):
    return {
        "apiVersion": "v1", "kind": "Secret",
        "metadata": {"name": "generated-bind-password", "namespace": "media", "resourceVersion": version},
        "data": {reference["key"]: base64.b64encode(value.encode()).decode()},
    }


def get(objects, kind):
    return next(item for item in objects if item["kind"] == kind)


def ready(objects):
    return next(condition["status"] for condition in get(objects, "PluginSettings")["status"]["conditions"]
                if condition["type"] == "Ready")


def payload(objects):
    return json.loads(base64.b64decode(get(objects, "Secret")["data"]["configuration.json"]))


with tempfile.TemporaryDirectory(prefix="jellyfin-composition-") as directory:
    work = Path(directory)
    (work / "function.json").write_text(json.dumps(function))

    def render(required, observed=(), source=xr):
        for name, content in (("xr", [source]), ("required", required), ("observed", observed)):
            (work / f"{name}.yaml").write_text("\n---\n".join(json.dumps(item) for item in content))
        command = ["crossplane", "render", str(work / "xr.yaml"), str(composition),
                   str(work / "function.json"), "--include-full-xr",
                   "--required-resources", str(work / "required.yaml")]
        if observed:
            command += ["--observed-resources", str(work / "observed.yaml")]
        (work / "rendered.yaml").write_bytes(subprocess.check_output(command))
        return read_yaml(work / "rendered.yaml")

    missing = render([])
    assert ready(missing) == "False" and len(missing) == 1
    initial = render([identity(), secret()])
    assert ready(initial) == "False"
    assert payload(initial)[field] == password
    assert payload(initial)["LdapServer"] == xr["spec"]["configuration"]["LdapServer"]
    settings = get(initial, "Secret")
    settings["metadata"]["resourceVersion"] = "10"
    observed_settings = render([identity(), secret()], [settings])
    managed = get(observed_settings, "PluginConfiguration")
    assert managed["spec"]["forProvider"]["configurationJsonSecretRef"]["name"] == settings["metadata"]["name"]
    assert settings["metadata"]["namespace"] == managed["metadata"]["namespace"] == "media"
    assert managed["spec"]["managementPolicies"] == xr["spec"]["managementPolicies"]
    assert managed["metadata"]["annotations"]["crossplane.io/external-name"] == xr["spec"]["pluginID"]
    managed["status"] = {"conditions": conditions}
    assert ready(render([identity(), secret()], [settings, managed])) == "True"

    missing_key = secret()
    missing_key["data"] = {}
    for required in ([identity(ready=False), secret("8", "rotated-password")],
                     [identity(), secret("8", "rotated-password")], [identity(), missing_key]):
        pending = render(required, [settings, managed])
        assert ready(pending) == "False"
        assert get(pending, "Secret")["data"] == settings["data"]
        assert get(pending, "PluginConfiguration")["spec"] == managed["spec"]
    rotated = render([identity("8"), secret("8", "rotated-password")], [settings, managed])
    assert ready(rotated) == "False" and payload(rotated)[field] == "rotated-password"

    direct = copy.deepcopy(xr)
    direct["spec"]["secretValues"][field] = {"name": "generated-bind-password", "key": reference["key"]}
    assert payload(render([secret()], source=direct))[field] == password

print("PluginSettings: nine credential publication, rotation and pending-state render cases passed")
