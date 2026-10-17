# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT

"""Check storage and exposure contracts after Kustomize and Flux substitution."""

import json
from pathlib import Path
import subprocess
import sys


def verify(path):
    output = subprocess.run(["yq", "eval-all", "-o=json", "-I=0", ".", str(path)],
                            capture_output=True, text=True, check=True).stdout
    objects = [json.loads(line) for line in output.splitlines() if line.strip()]
    objects = [item for item in objects if isinstance(item, dict)]

    def get(kind, name, namespace=None):
        matches = [item for item in objects if item.get("kind") == kind
                   and item.get("metadata", {}).get("name") == name
                   and (namespace is None or item["metadata"].get("namespace") == namespace)]
        if len(matches) != 1:
            raise ValueError(f"expected one {kind}/{name}, found {len(matches)}")
        return matches[0]

    assert get("ReplicationSource", "jellyfin", "media")["spec"]["sourcePVC"] == "jellyfin-checkpoint"
    destination_trigger = get("ReplicationDestination", "jellyfin-dst", "media")["spec"]["trigger"]
    assert destination_trigger["schedule"] == "15 8 * * *"
    assert not destination_trigger.get("manual"), "manual triggers suppress scheduled restore refresh"
    watcher = get("Deployment", "jellyfin-volsync-quiesce", "media")["spec"]["template"]["spec"]["containers"][0]
    environment = {item["name"]: item.get("value") for item in watcher["env"]}
    assert environment["PVC"] == "jellyfin-checkpoint"
    assert isinstance(environment["RESUME_CMD"], str)
    assert get("Role", "jellyfin-volsync-quiesce", "media")["rules"][0]["resourceNames"] == ["jellyfin-checkpoint"]
    annotation = "volsync.backube/use-copy-trigger"
    assert annotation in get("PersistentVolumeClaim", "jellyfin-checkpoint", "media")["metadata"]["annotations"]
    assert annotation not in get("PersistentVolumeClaim", "jellyfin", "media")["metadata"].get("annotations", {})
    storage = get("StorageClass", "jellyfin-media")
    assert storage["reclaimPolicy"] == "Delete"
    assert storage["parameters"]["onDelete"] == "retain"
    assert storage["parameters"]["subDir"] == "library"
    assert get("CSIDriver", "nfs.csi.k8s.io")["spec"]["fsGroupPolicy"] == "None"
    application = get("Deployment", "jellyfin", "media")["spec"]["template"]["spec"]
    assert any(item["name"] == "restore-media" for item in application["initContainers"])
    jellyfin = next(item for item in application["containers"] if item["name"] == "jellyfin")
    assert not next(item for item in jellyfin["volumeMounts"] if item["name"] == "media").get("readOnly", False)
    gateway = get("Gateway", "private", "network")
    assert gateway["spec"]["infrastructure"]["labels"]["bgp"] == "enabled"
    assert get("CiliumGatewayClassConfig", "private", "network")["spec"]["service"]["loadBalancerClass"] == "io.cilium/bgp-control-plane"
    assert get("CiliumLoadBalancerIPPool", "private-gateway")["spec"]["blocks"] == [{"cidr": "172.21.68.11/32"}]
    route = get("HTTPRoute", "jellyfin", "media")
    assert all(item["name"] == "private" and item["namespace"] == "network" for item in route["spec"]["parentRefs"])
    print("Jellyfin rendered storage, recovery and private-route contracts passed")


if __name__ == "__main__":
    verify(Path(sys.argv[1]))
