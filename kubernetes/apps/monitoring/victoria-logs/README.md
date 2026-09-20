<!--
SPDX-FileCopyrightText: 2026 Soup Bot <bot+ops@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# VictoriaLogs

VictoriaLogs stores seven days of Kubernetes container and node journal logs on a 40 GiB ZFS PVC.
The 32 GiB storage limit reserves the upstream-recommended 20% free space for merges and ingestion
bursts. Time and size retention apply independently, so high volume can shorten the effective window.

Vector runs once per node. It reads only `/var/log`, `/run/log/journal`, and the host machine ID required
by `journalctl`, which cover CRI container logs and persistent or volatile systemd journals used by RKE2.
Its writable host path contains only delivery checkpoints and a 512 MiB disk buffer. Vector needs UID 0
to read root-owned journal files,
drops all capabilities, uses the runtime-default seccomp profile, and has a read-only root filesystem.
The VictoriaLogs API is not exposed outside the cluster and a NetworkPolicy limits it to the monitoring
namespace because VictoriaLogs does not implement authorization itself.

Grafana discovers the provisioned `VictoriaLogs` datasource and bundled dashboards by label. The
Grafana deployment must install the signed `victoriametrics-logs-datasource` plugin before startup.

## Recovery contract

VolSync snapshots the PVC to the existing off-cluster restic repository every six hours. A clean
rebuild restores the newest snapshot through the standard `ReplicationDestination` volume-populator
flow. The generic policy retains seven daily and four weekly recovery points, so backup objects can
contain expired logs for roughly four weeks even though the live query retention is seven days.

VictoriaLogs documents filesystem snapshots as a supported backup method. These CSI snapshots are
crash-consistent rather than a portable per-partition export; recovery can lose up to six hours of logs
and buffered node data, but does not depend on a live VictoriaLogs process. For an older recovery point,
use `task volsync:restore-at APP=victoria-logs AT=<timestamp>`, then verify the restored partitions with
a LogSQL query before removing the rollback snapshot. A post-deployment restore drill is still required
before this becomes the accepted production recovery path.

The Vector checkpoint host path is deliberately not backed up. After node loss, Vector resumes from the
available journal and CRI files; entries already rotated off the rebuilt node cannot be recovered.
