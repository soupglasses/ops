<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# RKE2 recovery verification — 2026-09-26

The recovery used the repository and existing encrypted S3 backups. Legacy Talos
datasets remain untouched. New application volumes use `openebs-zfs` under
`zfspool/k8s`, on the five rotational disks. The ZFS pool was ONLINE with no reported errors.

## Application data

| Application | Recovery evidence |
| --- | --- |
| OpenLDAP | September 9 snapshot `e7909a31` and September 8 snapshot `1e88643a` each restored a 557,056-byte database and an 88-entry portable dump. TLS LDAP queries succeeded against the running directory. |
| Minecraft | September 9 snapshot `6e238433` and September 8 snapshot `85790c64` restored 17 region files and one player record. The restored server started and answered RCON; its voice listener started. |
| VictoriaMetrics | First usable snapshot `a1b8ba10` restored queryable samples. A second backup completed at 17:10:23 UTC; a separate native server queried 13,702 series over the preceding two hours from that S3 restore. |
| VictoriaLogs | No previous snapshots existed; the service was initialized empty. Snapshot `856ddb3c` restored 46,993 records. A second backup completed at 17:10:33 UTC; an isolated native server queried 34,830 journal records and 547,994 Kubernetes records, including 28 `vmsingle` and 120 `vlogs` records. |

The fresh LDAP backup restored the same database checksum and an 88-entry dump:
`c46a3f2aceb71e7777b3df2e5207c16090f21409cc6a93915ca2ead79de58455`.
The fresh Minecraft backup restored 17 region files, one player record and a
1,545-byte `level.dat`, checksum
`d516474715e1bb8d541fdb954f147e6ce2cda410844a822fe2bb93c7965fe33f`.
The quiesce watcher completed LDAP dump preparation and Minecraft's save-off,
save-all/flush and save-on sequence. All four scheduled backup sources are enabled.

Monitoring restore tests used new ReplicationDestinations and PVC data sources,
then ran the native database images against those isolated restored volumes.
The test pods, PVCs and ReplicationDestinations were deleted after validation.
Temporary application-recovery Kustomizations were orphan-deleted after normal Flux
Kustomizations adopted their workloads.

During bootstrap, a retention change restarted
the still-ephemeral metrics pod before its persistent volume was attached; up to
roughly 30 minutes of bootstrap metrics were lost. Subsequent collected samples
were exported and imported before attaching the ZFS PVC. The running metrics pod
now uses that PVC and its restored-data startup check passes.

## Access and control

Upstream DNS resolves `k8s.home.finnes.dev` to `172.21.71.10`. The RKE2 serving
certificate includes that name, and authenticated API requests through it succeed.
RKE2 returned to active state after the certificate-configuration restart.
`task kube:kubeconfig` refreshes local credentials after a clean rebuild.

LDAP's provisioning state was absent after the rebuild. Declarative imports now
adopt all 14 existing managed entries by DN. Names-only TLS queries verified these
entries before adoption, the controller applied successfully, and Canaille became
Ready. The imports remain in Git for the next clean-cluster recovery.

The collector initially exhausted 512 MiB while catching up. With a 1 GiB limit,
bounded batches and two concurrent requests, it ingested both Kubernetes and journal
logs without restarting during the subsequent restore verification. Its unsupported
Elasticsearch cluster-health request is disabled; VictoriaLogs remains monitored
through its own metrics and health checks.

Alertmanager email delivery was confirmed by the operator on 2026-09-26.

The earlier etcd collector and its snapshot history were retired on September 27.
Etcd backups are no longer part of recovery; see the [clean bootstrap guide](cluster-bootstrap.md).
The application-level restore evidence above remains relevant to that path.

## ZFS clone cleanup

The initial VictoriaMetrics and VictoriaLogs PVCs are clones of empty bootstrap
snapshots. Refreshing their restore destinations retired those source datasets,
but ZFS correctly refused their deletion while the application clones depended on
them. OpenEBS consequently retried deletion every 30 seconds. This is a dependency
error, not evidence of pool corruption.

Read-only `zdb` inspection confirmed both retired sources contain only six metadata
objects and an empty root directory, using 153 KiB each. The latest successful S3
backup IDs were `3931eb22` for metrics at 18:37 UTC and `10f20693` for logs at
18:17 UTC. The current restore destinations reference separate newer images.

The repair completed on September 26 after independent review using [ZFS clone promotion](https://openzfs.github.io/openzfs-docs/man/master/8/zfs-promote.8.html).
Promotion reverses the dependency without moving the live dataset or replacing its
contents. The existing OpenEBS deletion requests can then finish normally.

Both promotions preserved dataset GUIDs, mounted paths, PVC bindings and 100/200 GiB
quotas. Closed-window queries for 18:00–18:10 UTC returned the same 40 metric samples
and 2,226 log records before and after each operation. Fresh ingestion continued,
neither application restarted, and OpenEBS removed both retired sources normally.
The transferred bootstrap snapshots remain on the live datasets.

Before promoting, verify the current PVC-to-dataset mapping, recent off-cluster
backups and the application restore evidence above. On the owning node, inspect
the exact origin and its snapshots with `zfs list -r -t filesystem,snapshot
-o name,origin,used,refer,clones zfspool/k8s`. Proceed only when the retired dataset
has no PVC, its ZFSVolume is already deleting, its origin snapshot has exactly the
intended application clone, and that clone has no conflicting snapshot names.
Review any additional clones or snapshots before changing the dependency.

For the verified September 26 mapping, the individual operations are:

```bash
# VictoriaMetrics
sudo zfs promote zfspool/k8s/pvc-132e142d-30d5-4eb3-80d2-086410fb8148
# VictoriaLogs
sudo zfs promote zfspool/k8s/pvc-6cc620f0-8099-40e0-b1bb-4334dbcb89d3
```

After each operation, require the promoted dataset's `origin` to be `-`, unchanged
PVC binding and successful historical application queries. Let OpenEBS remove the
already-deleting source and snapshot objects. Keep the transferred bootstrap
snapshot on the promoted dataset. Do not use recursive dependent-clone destruction
(`zfs destroy -R`) or remove finalizers to bypass the dependency. OpenEBS 2.9.0
[treats an absent snapshot at its old path as completed deletion](https://github.com/openebs/zfs-localpv/blob/v2.9.0/pkg/zfs/zfs_util.go#L697).

This procedure applies to retired restore sources, not to the current
ReplicationDestination image used for future PVC recovery. Do not promote or
delete the active OpenLDAP or Minecraft restore sources as part of this repair.

Snapshot restores create dependent clones. OpenEBS ZFS 2.11.1 still
[does not promote them automatically](https://github.com/openebs/zfs-localpv/blob/v2.11.1/pkg/zfs/zfs_util.go#L714).
After future restores, inspect dependencies before retiring the source image.
Sources containing application data require a fresh recovery assessment before
promotion or cleanup.

Use the maintenance task to inspect a PVC without changing storage:

```bash
task volsync:zfs-inspect -- victoria-metrics --namespace monitoring
```

It reports the dataset GUID, origin, dependencies and promotion blockers through
the existing verified SSH connection to `nas`. A dataset with no origin needs no
promotion. For a reviewed, empty retired bootstrap source, copy the exact GUID
and origin from inspection:

```bash
task volsync:zfs-promote -- victoria-metrics --namespace monitoring \
  --expect-guid GUID --expect-origin POOL/DATASET@SNAPSHOT --verified-recovery
```

`--verified-recovery` records that the operator has checked the off-cluster
application restore evidence. The task also requires a successful idle backup
within 24 hours, checks both source and snapshot metadata for emptiness, rejects
active snapshot references and revalidates the mapping before promotion. It
retains the transferred snapshot and never deletes datasets or removes
finalizers. Verify historical queries and fresh writes afterward, then confirm
OpenEBS has completed its existing deletion requests.

The automated promotion deliberately refuses data-bearing origins and unfamiliar
ZFS metadata layouts. Such restores need the reviewed procedure above rather
than bypassing the checks.

## Artifact recovery

Collector and recovery-init image digests are pinned in the workload manifests.
The registry manifests were fetched and their SHA-256 digests verified on 2026-09-26.

| Image | Source and build path |
| --- | --- |
| Vector 0.55.0 Debian | [Source commit](https://github.com/vectordotdev/vector/tree/cf8de830c6060629092397fd2334fc0f0219363a), including `distribution/docker/debian/Dockerfile`. |
| VictoriaMetrics 1.149.0 | [Source commit](https://github.com/VictoriaMetrics/VictoriaMetrics/tree/c525ea4b2817a5f3c24ca4b04f6bef283d874d53) and [container build instructions](https://docs.victoriametrics.com/victoriametrics/single-server-victoriametrics/#building-docker-images). |
| VictoriaLogs 1.50.0 | [Source commit](https://github.com/VictoriaMetrics/VictoriaLogs/tree/804086be444d5bae008ee538c02a6c45f9da1011) and [build instructions](https://docs.victoriametrics.com/victorialogs/#building-from-sources). |
| OpenLDAP 2.6.10 | [Source and container build files](https://github.com/soupglasses/home-ops-containers/tree/9e717ce9681d9ad48638e3e4a5eced88f311b893/apps/openldap). |

Keep copies of these sources with the recovery material. If an image becomes
unavailable, rebuild from its recorded source and publish it to a reachable registry.
Validate the replacement against an isolated restored volume before updating the
deployment digest. A rebuild can produce a different digest from the original release.

## Remaining acceptance work

Final specialist review, Flux host-policy adoption, controlled host reboot and
post-reboot checks remain open. Full etcd disaster restoration on a replacement
machine has not been proven by these application drills. Router automation-account
retirement waits for an independently stored encrypted RouterOS backup. Legacy
datasets remain preserved.
