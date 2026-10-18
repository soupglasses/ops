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

    retired = {
        ("ReplicationSource", "jellyfin"),
        ("ReplicationSource", "jellyfin-media"),
        ("ReplicationDestination", "jellyfin-dst"),
        ("Deployment", "jellyfin-volsync-quiesce"),
        ("Role", "jellyfin-volsync-quiesce"),
        ("RoleBinding", "jellyfin-volsync-quiesce"),
        ("ServiceAccount", "jellyfin-volsync-quiesce"),
        ("PersistentVolumeClaim", "jellyfin-checkpoint"),
        ("Job", "jellyfin-media-restore-v2"),
        ("Kustomization", "jellyfin-media-backup"),
    }
    for item in objects:
        metadata = item.get("metadata", {})
        if metadata.get("namespace") == "media":
            assert (item.get("kind"), metadata.get("name")) not in retired, "Jellyfin backup resource remains"
    config = get("PersistentVolumeClaim", "jellyfin", "media")
    assert "dataSourceRef" not in config["spec"]
    assert "dataSource" not in config["spec"]
    assert config["spec"]["resources"]["requests"]["storage"] == "20Gi"
    assert config["metadata"]["annotations"]["kustomize.toolkit.fluxcd.io/ssa"] == "IfNotPresent"
    assert get("PersistentVolumeClaim", "jellyfin-media", "media")["spec"]["storageClassName"] == "jellyfin-media"
    storage = get("StorageClass", "jellyfin-media")
    assert storage["reclaimPolicy"] == "Delete"
    assert storage["parameters"]["onDelete"] == "retain"
    assert storage["parameters"]["subDir"] == "library"
    assert get("CSIDriver", "nfs.csi.k8s.io")["spec"]["fsGroupPolicy"] == "None"
    application = get("Deployment", "jellyfin", "media")["spec"]["template"]["spec"]
    assert not application.get("initContainers"), "Jellyfin must start without backup restores"
    assert not any(item["name"] == "checkpoint" for item in application["volumes"])
    jellyfin = next(item for item in application["containers"] if item["name"] == "jellyfin")
    for probe in ("startupProbe", "readinessProbe"):
        assert jellyfin[probe]["exec"]["command"] == ["python3", "/usr/local/lib/jellyfin/health.py"]
    assert not next(item for item in jellyfin["volumeMounts"] if item["name"] == "media").get("readOnly", False)
    gateway = get("Gateway", "private", "network")
    assert gateway["spec"]["infrastructure"]["labels"]["bgp"] == "enabled"
    assert get("CiliumGatewayClassConfig", "private", "network")["spec"]["service"]["loadBalancerClass"] == "io.cilium/bgp-control-plane"
    assert get("CiliumLoadBalancerIPPool", "private-gateway")["spec"]["blocks"] == [{"cidr": "172.21.68.11/32"}]
    route = get("HTTPRoute", "jellyfin", "media")
    assert all(item["name"] == "private" and item["namespace"] == "network" for item in route["spec"]["parentRefs"])
    print("Jellyfin rendered storage, no-backup policy and private-route contracts passed")


if __name__ == "__main__":
    verify(Path(sys.argv[1]))
