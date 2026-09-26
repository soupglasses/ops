#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
#
# SPDX-License-Identifier: 0BSD

"""Exercise maintenance commands against Kubernetes/ZFS command response fixtures."""

import contextlib
import datetime
import importlib.util
import io
import json
import pathlib
import socket
import sys
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("zfs_clone", pathlib.Path(__file__).with_name("zfs-clone.py"))
TOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOL)
TARGET = "pool/k8s/pvc-target"
SOURCE = "pool/k8s/pvc-source"
ORIGIN = SOURCE + "@snapshot-empty"


def empty_zdb():
    # Structural fields from zdb -dddd for the verified empty bootstrap datasets.
    text = "Dataset empty [ZPL], ID 926, cr_txg 5464778, 153K, 6 objects, rootbp\n"
    for number in (-3, -2, -1, 0, 1, 32, 33, 34, 35, 36):
        kind = "ZFS directory" if number == 34 else "metadata"
        text += f"\n    Object  lvl   iblk   dblk  dsize  dnsize  lsize   %full  type\n {number} 1 128K 512 0 512 512 100.00 {kind}\n"
        if number == 1:
            text += " ROOT = 34 \n"
        if number in (33, 34):
            text += " microzap: 512 bytes, 0 entries\n"
    return text


class Responses:
    def __init__(self):
        self.props = {"guid": "123", "origin": ORIGIN, "quota": "100000000",
                      "refquota": "0", "mountpoint": "legacy", "mounted": "yes", "referenced": "2000000"}
        self.source = dict(self.props, guid="456", origin="-", mounted="no", referenced="156672")
        self.pvc = {"metadata": {"uid": "uid-target"}, "spec": {"volumeName": "pvc-target"}, "status": {"phase": "Bound"}}
        self.pv = {"metadata": {"name": "pvc-target"}, "spec": {"claimRef": {"uid": "uid-target"},
                   "csi": {"driver": "zfs.csi.openebs.io", "volumeHandle": "pvc-target"}}}
        self.volume = {"spec": {"volumeType": "DATASET", "ownerNodeID": socket.gethostname(), "poolName": "pool/k8s"}}
        self.source_volume = {"metadata": {"name": "pvc-source", "deletionTimestamp": "2026-09-26T00:00:00Z"}, "spec": {"poolName": "pool/k8s", "ownerNodeID": socket.gethostname()}}
        self.backup = {"metadata": {"namespace": "monitoring"}, "spec": {"sourcePVC": "metrics"}, "status": {
            "lastSyncTime": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "latestMoverStatus": {"result": "Successful"}}}
        self.collections = {"pvc": [self.pvc], "pv": [self.pv], "zfsvolumes": [self.source_volume],
                            "volumesnapshotcontents": [], "volumesnapshots": [],
                            "replicationdestinations": [], "replicationsources": [self.backup]}
        self.clones = TARGET
        self.zdb = empty_zdb()
        self.child_datasets = SOURCE
        self.mutations = []

    def execute(self, args, **kwargs):
        if "kubectl" in args[2]:
            tail = args[args.index("get") + 1:]
            resource = tail[0]
            if tail[1].startswith("-"):
                return json.dumps({"items": self.collections[resource]})
            return json.dumps({"pvc": self.pvc, "pv": self.pv, "zfsvolume": self.volume}[resource])
        command, rest = args[2], args[3:]
        if command == "zpool":
            return "ONLINE"
        if command == "zdb":
            return self.zdb
        if command == "zfs":
            if rest[0] == "promote":
                self.mutations.append(rest)
                self.props["origin"] = "-"
                return ""
            if rest[0] == "list":
                if "filesystem,volume" in rest:
                    return self.child_datasets
                if rest[-1] == SOURCE:
                    return ORIGIN
                return TARGET + "@snapshot-empty" if self.mutations else ""
            if rest[-2] == TOOL.PROPERTIES:
                props = self.props if rest[-1] == TARGET else self.source
                return "\n".join(k + "\t" + v for k, v in props.items())
            if rest[-2] == "clones":
                return self.clones
            if rest[-2] == "referenced":
                return self.source["referenced"]
        raise AssertionError(args)


class MaintenanceTest(unittest.TestCase):
    def invoke(self, responses, action="promote", extra=None):
        args = ["zfs-clone.py", action, "metrics", "-n", "monitoring", "--on-node"]
        args += extra if extra is not None else ["--expect-guid", "123", "--expect-origin", ORIGIN, "--verified-recovery"]
        with patch.object(sys, "argv", args), patch.object(TOOL.subprocess, "check_output", side_effect=responses.execute), contextlib.redirect_stdout(io.StringIO()):
            return TOOL.main()

    def test_inspection_never_mutates(self):
        data = Responses()
        self.invoke(data, "inspect")
        self.assertEqual(data.mutations, [])

    def test_reviewed_empty_source_is_promoted_once(self):
        data = Responses()
        self.invoke(data)
        self.assertEqual(data.mutations, [("promote", TARGET)])

    def test_refusals_do_not_mutate(self):
        cases = {
            "additional clone": lambda d: setattr(d, "clones", TARGET + ",pool/other"),
            "child dataset": lambda d: setattr(d, "child_datasets", SOURCE + "\n" + SOURCE + "/child"),
            "mounted source": lambda d: d.source.update(mounted="yes"),
            "nonempty source": lambda d: d.source.update(referenced="1000000"),
            "directory entries": lambda d: setattr(d, "zdb", d.zdb.replace("0 entries", "1 entries")),
            "unfamiliar metadata": lambda d: setattr(d, "zdb", "unknown zdb layout"),
            "source not retired": lambda d: d.source_volume["metadata"].pop("deletionTimestamp"),
            "source PV": lambda d: d.collections["pv"].append({"spec": {"csi": {"volumeHandle": "pvc-source"}}}),
            "active CSI handle": lambda d: d.collections["volumesnapshotcontents"].append({"metadata": {"name": "active"}, "spec": {"source": {"snapshotHandle": "pvc-source@snapshot-empty"}}}),
            "unresolved current restore": lambda d: d.collections["replicationdestinations"].append({"metadata": {"namespace": "monitoring"}, "status": {"latestImage": {"name": "missing"}}}),
            "failed backup": lambda d: d.backup["status"]["latestMoverStatus"].update(result="Failed"),
            "stale backup": lambda d: d.backup["status"].update(lastSyncTime="2000-01-01T00:00:00Z"),
            "wrong PV binding": lambda d: d.pv["spec"]["claimRef"].update(uid="different"),
        }
        for name, change in cases.items():
            with self.subTest(name=name):
                data = Responses()
                change(data)
                with self.assertRaises(ValueError):
                    self.invoke(data)
                self.assertEqual(data.mutations, [])

    def test_expected_identity_and_recovery_attestation_are_required(self):
        for extra in ([], ["--expect-guid", "999", "--expect-origin", ORIGIN, "--verified-recovery"]):
            data = Responses()
            with self.assertRaises(ValueError):
                self.invoke(data, extra=extra)
            self.assertEqual(data.mutations, [])

    def test_already_independent_dataset_does_not_promote(self):
        data = Responses()
        data.props["origin"] = "-"
        self.invoke(data, "inspect")
        with self.assertRaises(ValueError):
            self.invoke(data)
        self.assertEqual(data.mutations, [])


if __name__ == "__main__":
    unittest.main()
