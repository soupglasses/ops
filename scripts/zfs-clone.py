#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
#
# SPDX-License-Identifier: 0BSD

"""Inspect restored PVC dependencies and promote reviewed empty bootstrap clones."""

import argparse
import datetime
import json
import os
import pathlib
import re
import shlex
import socket
import subprocess
import sys

KUBECTL = ["sudo", "-n", "/var/lib/rancher/rke2/bin/kubectl",
           "--kubeconfig", "/etc/rancher/rke2/rke2.yaml", "--request-timeout=20s"]
PROPERTIES = "guid,origin,quota,refquota,mountpoint,mounted,referenced"


def run(*args):
    return subprocess.check_output(args, text=True, timeout=60).strip()


def kube(resource, name=None, namespace=None):
    args = [*KUBECTL, "get", resource]
    if name:
        args.append(name)
    if namespace:
        args.extend(["-n", namespace])
    elif resource not in ("pv", "volumesnapshotcontents"):
        args.append("-A")
    return json.loads(run(*args, "-o", "json"))


def zfs(*args):
    return run("sudo", "-n", "zfs", *args)


def properties(dataset):
    return dict(line.split("\t", 1) for line in
                zfs("get", "-Hp", "-o", "property,value", PROPERTIES, dataset).splitlines())


def empty_metadata(output):
    """Accept only the observed empty ZPL layout, refusing unfamiliar zdb output."""
    if not re.search(r"\[ZPL\].*, 6 objects,", output):
        return False
    if not re.search(r"\bROOT = 34\s", output):
        return False
    sections = re.split(r"\n\s+Object\s+lvl[^\n]*\n", output)[1:]
    objects = {}
    for section in sections:
        match = re.match(r"\s*(-?\d+)\s+[^\n]+", section)
        if not match:
            return False
        objects[int(match[1])] = section
    if set(objects) != {-3, -2, -1, 0, 1, 32, 33, 34, 35, 36}:
        return False
    return all("microzap: 512 bytes, 0 entries" in objects[number]
               for number in (33, 34)) and "ZFS directory" in objects[34].splitlines()[0]


def inspect(namespace, name):
    pvc = kube("pvc", name, namespace)
    if pvc.get("status", {}).get("phase") != "Bound" or pvc["metadata"].get("deletionTimestamp"):
        raise ValueError("PVC must be Bound and not deleting")
    pv = kube("pv", pvc["spec"]["volumeName"])
    if pv["metadata"].get("deletionTimestamp"):
        raise ValueError("PV is deleting")
    csi = pv["spec"].get("csi", {})
    if csi.get("driver") != "zfs.csi.openebs.io":
        raise ValueError("PVC is not backed by OpenEBS ZFS")
    if pv["spec"]["claimRef"]["uid"] != pvc["metadata"]["uid"]:
        raise ValueError("PV claim UID does not match PVC")
    handle = csi["volumeHandle"]
    volume = kube("zfsvolume", handle, "openebs-system")
    if volume.get("metadata", {}).get("deletionTimestamp"):
        raise ValueError("Target ZFSVolume is deleting")
    spec = volume["spec"]
    if spec.get("volumeType") != "DATASET" or spec.get("ownerNodeID") != socket.gethostname():
        raise ValueError("PVC must be a ZFS dataset owned by this node")
    dataset = spec["poolName"] + "/" + handle
    before = properties(dataset)
    report = {"pvc": namespace + "/" + name, "pvc_uid": pvc["metadata"]["uid"],
              "volume": pv["metadata"]["name"], "dataset": dataset,
              "properties": before, "blockers": []}
    blockers = report["blockers"]
    origin = before["origin"]
    if origin == "-":
        blockers.append("Dataset has no origin dependency")
        return report
    source, snapshot = origin.rsplit("@", 1)
    if source.rsplit("/", 1)[0] != spec["poolName"]:
        blockers.append("Origin is outside the target CSI pool")
        return report
    source_handle = source.rsplit("/", 1)[1]
    source_props = properties(source)
    report["source"] = {"dataset": source, "properties": source_props,
                        "snapshot": snapshot, "clones": zfs("get", "-H", "-o", "value", "clones", origin)}
    snapshots = zfs("list", "-H", "-t", "snapshot", "-o", "name", "-r", source).splitlines()
    datasets = zfs("list", "-H", "-t", "filesystem,volume", "-o", "name", "-r", source).splitlines()
    target_snapshots = zfs("list", "-H", "-t", "snapshot", "-o", "name", "-r", dataset).splitlines()
    report["source"]["snapshots"] = snapshots
    checks = [
        (run("sudo", "-n", "zpool", "list", "-H", "-o", "health", dataset.split("/")[0]) == "ONLINE", "Pool is not ONLINE"),
        (before["mounted"] == "yes", "Target is not mounted"),
        (source_props["mounted"] == "no", "Source is mounted"),
        (source_props["origin"] == "-", "Source is itself a clone"),
        (snapshots == [origin], "Source has additional snapshots or descendants"),
        (datasets == [source], "Source has descendant datasets"),
        (report["source"]["clones"] == dataset, "Origin does not have exactly the target clone"),
        (dataset + "@" + snapshot not in target_snapshots, "Target has a conflicting snapshot name"),
    ]
    for resource in ("pv", "pvc"):
        objects = kube(resource)["items"]
        if resource == "pv":
            referenced = any(i.get("spec", {}).get("csi", {}).get("volumeHandle") == source_handle for i in objects)
        else:
            referenced = any(i.get("spec", {}).get("volumeName") == source_handle for i in objects)
        checks.append((not referenced, "Source still has a " + resource.upper()))
    sources = [i for i in kube("zfsvolumes")["items"]
               if i["metadata"]["name"] == source_handle and i["spec"]["poolName"] == spec["poolName"]]
    checks.append((len(sources) == 1 and bool(sources[0]["metadata"].get("deletionTimestamp"))
                   and sources[0]["spec"].get("ownerNodeID") == spec["ownerNodeID"],
                   "Source ZFSVolume is not uniquely identified and deleting"))
    contents = kube("volumesnapshotcontents")["items"]
    retired_contents = set()
    for content in contents:
        handles = [content.get("status", {}).get("snapshotHandle"),
                   content["spec"].get("source", {}).get("snapshotHandle")]
        if (source_handle + "@" + snapshot in handles or origin in handles
                or content["spec"].get("source", {}).get("volumeHandle") == source_handle):
            retired_contents.add(content["metadata"]["name"])
    checks.append((not retired_contents, "CSI snapshot content still references the origin"))
    images = {(i["metadata"]["namespace"], i["metadata"]["name"]): i
              for i in kube("volumesnapshots")["items"]}
    for rd in kube("replicationdestinations")["items"]:
        image_name = rd.get("status", {}).get("latestImage", {}).get("name")
        if not image_name:
            continue
        image = images.get((rd["metadata"]["namespace"], image_name))
        if image is None:
            checks.append((False, "A restore destination's latest image cannot be resolved"))
        elif (image.get("status", {}).get("boundVolumeSnapshotContentName") in retired_contents
              or image.get("spec", {}).get("source", {}).get("volumeSnapshotContentName") in retired_contents):
            checks.append((False, "Origin is a current restore destination image"))
    backups = [i for i in kube("replicationsources")["items"]
               if i["metadata"]["namespace"] == namespace and i["spec"].get("sourcePVC") == name]
    healthy_backup = False
    for backup in backups:
        status = backup.get("status", {})
        when = status.get("lastSyncTime")
        if not when:
            continue
        age = datetime.datetime.now(datetime.timezone.utc) - datetime.datetime.fromisoformat(when.replace("Z", "+00:00"))
        healthy_backup |= (datetime.timedelta(0) <= age <= datetime.timedelta(hours=24)
                           and status.get("latestMoverStatus", {}).get("result") == "Successful"
                           and not any(c.get("type") == "Synchronizing" and c.get("status") == "True"
                                       for c in status.get("conditions", [])))
    checks.append((healthy_backup, "No successful idle backup within 24 hours for this PVC"))
    for ok, message in checks:
        if not ok:
            blockers.append(message)
    # Size alone does not prove emptiness. Inspect metadata of both filesystem and origin.
    for ds in (source, origin):
        if int(zfs("get", "-Hp", "-o", "value", "referenced", ds)) > 262144:
            blockers.append("Source or snapshot contains more than empty filesystem metadata")
            break
        if not empty_metadata(run("sudo", "-n", "zdb", "-dddd", ds)):
            blockers.append("Source or snapshot is nonempty or has an unrecognized metadata layout")
            break
    return report


def operate(args):
    report = inspect(args.namespace, args.pvc)
    print(json.dumps(report, indent=2), flush=True)
    if args.action == "inspect":
        return
    if not args.verified_recovery:
        raise ValueError("Promotion requires --verified-recovery after reviewing off-cluster restore evidence")
    before = report["properties"]
    if (args.expect_guid, args.expect_origin) != (before["guid"], before["origin"]):
        raise ValueError("Expected GUID/origin do not match the current PVC")
    if report["blockers"]:
        raise ValueError("Promotion refused: " + "; ".join(report["blockers"]))
    # Repeat all guards immediately before the sole mutation, including the PVC UID.
    current = inspect(args.namespace, args.pvc)
    # Application writes may change referenced bytes while identity and dependencies stay fixed.
    current["properties"].pop("referenced")
    previous = json.loads(json.dumps(report))
    previous["properties"].pop("referenced")
    if current != previous:
        raise ValueError("Storage state changed during inspection, inspect again")
    dataset = report["dataset"]
    zfs("promote", dataset)
    after = properties(dataset)
    if after["origin"] != "-" or any(before[k] != after[k] for k in
                                        ("guid", "quota", "refquota", "mountpoint", "mounted")):
        raise ValueError("Post-promotion properties changed unexpectedly, stop and investigate")
    pvc = kube("pvc", args.pvc, args.namespace)
    if pvc["metadata"]["uid"] != report["pvc_uid"] or pvc["spec"]["volumeName"] != report["volume"]:
        raise ValueError("PVC binding changed, stop and investigate")
    snapshot = before["origin"].rsplit("@", 1)[1]
    zfs("list", "-H", "-t", "snapshot", dataset + "@" + snapshot)
    print("Promoted. Keep the transferred snapshot. Verify application history and fresh writes,")
    print("then confirm OpenEBS finishes its existing deletion requests. No resources were deleted by this task.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["inspect", "promote"])
    parser.add_argument("pvc")
    parser.add_argument("--namespace", "-n", default="default")
    parser.add_argument("--expect-guid")
    parser.add_argument("--expect-origin")
    parser.add_argument("--verified-recovery", action="store_true")
    parser.add_argument("--on-node", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.on_node:
        command = shlex.join(["python3", "-", *sys.argv[1:], "--on-node"])
        result = subprocess.run(["ssh", "-F", "/dev/null", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                                 "-i", os.path.expanduser("~/.ssh/gpg.pub"), "-o", "StrictHostKeyChecking=yes",
                                 "-o", "HostKeyAlias=nas.home.finnes.dev", "sofie@172.21.71.10", command],
                                input=pathlib.Path(__file__).read_text(), text=True)
        return result.returncode
    operate(args)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, KeyError, subprocess.SubprocessError) as error:
        print("ERROR:", error, file=sys.stderr)
        sys.exit(1)
