<!--
SPDX-FileCopyrightText: 2026 OpenAI

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

## Remaining acceptance work

Final specialist review, Flux host-policy adoption, controlled host reboot and
post-reboot checks remain open. Full etcd disaster restoration on a replacement
machine has not been proven by these application drills. Router automation-account
retirement waits for an independently stored encrypted RouterOS backup. Legacy
datasets remain preserved.
