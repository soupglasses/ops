# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT

"""Render monitor ID resolution, recovery and pending-state preservation."""

import copy
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
composition = root / "kubernetes/apps/crossplane-system/crossplane/providers/uptime-kuma/composition.yaml"


def read_yaml(path):
    return json.loads(subprocess.check_output(["yq", "eval-all", "-o=json", "[.]", str(path)]))


xr = read_yaml(root / "kubernetes/apps/monitoring/uptime-kuma/app/publicstatuspage.yaml")[0]
xr["metadata"].update(namespace="monitoring", uid="synthetic-status-page")
function = next(item for item in read_yaml(root / "kubernetes/apps/identity/ldap-provider/app/function.yaml")
                if item["kind"] == "Function")
refs = [ref for group in xr["spec"]["groups"] for ref in group["monitorRefs"]]


def monitors(ids=(101, 202)):
    return [{"apiVersion": "uptimekuma.ops.finnes.dev/v1alpha1", "kind": ref["kind"],
             "metadata": {"name": ref["name"], "namespace": "monitoring"},
             "spec": {"providerConfigRef": {"kind": "ProviderConfig", "name": "default"}},
             "status": {"atProvider": {"id": str(identifier)}, "conditions": [
                 {"type": kind, "status": "True"} for kind in ("Ready", "Synced")]}}
            for ref, identifier in zip(refs, ids)]


def get(objects, kind):
    return next(item for item in objects if item["kind"] == kind)


def ready(objects):
    return next(c["status"] for c in get(objects, "PublicStatusPage")["status"]["conditions"]
                if c["type"] == "Ready")


with tempfile.TemporaryDirectory(prefix="status-page-composition-") as directory:
    work = Path(directory)
    (work / "function.yaml").write_text(json.dumps(function))

    def render(required, observed=()):
        for name, content in (("xr", [xr]), ("required", required), ("observed", observed)):
            (work / f"{name}.yaml").write_text("\n---\n".join(json.dumps(item) for item in content))
        command = shlex.split(os.environ.get("CROSSPLANE_COMMAND", "crossplane"))
        command += ["render", str(work / "xr.yaml"), str(composition), str(work / "function.yaml"),
                    "--include-full-xr", "--required-resources", str(work / "required.yaml")]
        if os.environ.get("CROSSPLANE_CORE_BINARY"):
            command += ["--crossplane-binary", os.environ["CROSSPLANE_CORE_BINARY"]]
        else:
            version = read_yaml(root / "kubernetes/apps/crossplane-system/crossplane/app/helmrelease.yaml")[0]["spec"]["chart"]["spec"]["version"]
            command += ["--crossplane-version", "v" + version]
        if observed:
            command += ["--observed-resources", str(work / "observed.yaml")]
        (work / "rendered.yaml").write_bytes(subprocess.check_output(command))
        return read_yaml(work / "rendered.yaml")

    missing = render([])
    assert ready(missing) == "False" and len(missing) == 1
    initial = render(monitors())
    page = get(initial, "StatusPage")
    assert ready(initial) == "False"
    assert [m["id"] for m in page["spec"]["forProvider"]["publicGroupList"][0]["monitorList"]] == [101, 202]
    assert "Delete" not in page["spec"]["managementPolicies"]
    assert page["metadata"]["namespace"] == "monitoring"
    page["status"] = {"atProvider": copy.deepcopy(page["spec"]["forProvider"]),
                      "conditions": [{"type": kind, "status": "True"} for kind in ("Ready", "Synced")]}
    page["status"]["atProvider"]["id"] = "303"
    page["status"]["atProvider"]["publicGroupList"][0]["id"] = 901
    assert ready(render(monitors(), [page])) == "True"
    recovered = render(monitors((103, 204)), [page])
    assert [m["id"] for m in get(recovered, "StatusPage")["spec"]["forProvider"]["publicGroupList"][0]["monitorList"]] == [103, 204]
    assert ready(recovered) == "False"
    cases = [[], monitors()]
    cases[1][0]["status"]["conditions"][1]["status"] = "False"
    wrong_namespace = monitors()
    wrong_namespace[0]["metadata"]["namespace"] = "elsewhere"
    wrong_backend = monitors()
    wrong_backend[0]["spec"]["providerConfigRef"]["name"] = "another-server"
    for required in cases + [wrong_namespace, wrong_backend]:
        pending = render(required, [page])
        assert ready(pending) == "False"
        assert get(pending, "StatusPage")["spec"] == page["spec"]
    print("Page composition resolves live IDs, follows recreated monitors and retains the page while references are unavailable.")
