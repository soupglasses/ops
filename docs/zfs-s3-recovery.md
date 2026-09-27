<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# ZFS rebuild and S3 recovery

The imported `zfspool` is a five-disk RAIDZ1 pool left by Talos. Its old `pvc-*`
datasets have no Kubernetes metadata and are not adopted into the rebuilt cluster.
VolSync/restic in S3 is the authoritative recovery path.

This is deliberately a two-pass recovery. It proves remote recovery before erasing the
remaining local copy, then proves the same recovery onto the final storage layout.

## Destructive gate

Do not destroy the pool, clear ZFS labels, delete old datasets, or remove the Talos
configuration until all of these are true:

- the `resilience` profile approved the exact change;
- every old dataset was inventoried without writing to it;
- every required S3 repository restored into a fresh PVC;
- application-level contents were validated;
- the restore evidence and recovery keys were recorded outside the cluster;
- the operator explicitly approved the RAIDZ1-to-RAIDZ2 destruction.

RAIDZ1 cannot be converted to RAIDZ2 in place.

## Pass 1: prove S3 while preserving the old pool

1. Bring up RKE2 on the NVMe. Do not mount old `pvc-*` datasets into workloads.
2. Inventory the pool:

   ```bash
   sudo zpool status -P zfspool
   sudo zfs list -r -t filesystem,snapshot -o name,used,refer,mountpoint,mounted zfspool
   ```

   The read-only inventory on 2026-09-21 found the unmounted root plus six unmounted
   legacy datasets: `pvc-2e2da4b0-eba4-4906-a9c1-c3b33ade8935`,
   `pvc-4ed97cc6-4985-45cb-b240-c401a75f50d6`,
   `pvc-5193448c-ccf6-4bb4-8415-72f161454648`,
   `pvc-78cffec4-94e5-448a-973c-bb87f0d568f4`,
   `pvc-ad295d14-60b1-43f5-9e03-a3d0ac138e20`, and
   `pvc-fc9a71dd-4fd3-4c93-8833-1187258185e9`. None is mounted and
   `zfspool/k8s` does not yet exist.

3. Create the empty parent `zfspool/k8s` through the explicit Ansible switch.
4. Bootstrap Flux and let OpenEBS create new datasets beneath that parent.
5. Let each VolSync `ReplicationDestination` pull from S3 and populate a fresh PVC.
6. Validate the applications:
   - OpenLDAP: compare expected DNs/schema and complete a Canaille login;
   - Minecraft: verify world/player data, join the server, and test voice UDP;
   - any later database: use its native integrity check and application query.
7. Trigger a new backup from the restored data and perform a second isolated restore.

The existing `scripts/volsync-restore.sh` creates a local rollback `VolumeSnapshot`
before replacing a PVC. It leaves that snapshot behind deliberately. Delete it only
after application validation.

## Recreate the pool

This step is intentionally not automated by the normal Ansible convergence playbook.
After the gate is satisfied, stop RKE2 and export the final inventory. The approved
operator procedure should then:

1. destroy the old pool and clear only the five explicitly verified data disks;
2. create `zfspool` as five-disk RAIDZ2 with `ashift=12` and stable by-id paths;
3. set `compression=zstd`, `atime=off`, `xattr=sa`, and the reviewed ACL properties;
4. create `zfspool/k8s` with `mountpoint=none`;
5. enable ZFS import, event daemon, SMART monitoring, and monthly scrub timers;
6. restart RKE2 and reconcile Flux.

Five 4 TB disks provide roughly 10.9 TB usable in RAIDZ2, instead of roughly 14.4 TB
in RAIDZ1, in exchange for surviving two disk failures.

## Pass 2: prove clean rebuild

Recreate every PVC through Flux and VolSync. Repeat the application checks from pass 1,
then verify:

```bash
kubectl get pvc,volumesnapshot -A
kubectl get replicationsources,replicationdestinations -A
sudo zfs list -r zfspool/k8s
sudo zpool status -x zfspool
```

Run a disposable storage drill: write a checksum payload, snapshot it, restore it into
a second PVC, compare the checksum, and delete every throwaway resource. Test both
`Delete` and `Retain` StorageClasses.

Only after pass 2 may the old recovery artifacts and Talos files be retired. Keep S3
retention long enough for delayed corruption detection and ensure the in-cluster writer
cannot erase the only independent copy.
