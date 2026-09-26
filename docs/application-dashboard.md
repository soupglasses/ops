<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# Application overview

Open **Application overview** in Grafana and select a namespace and one or more apps.
Select **All** to combine every labelled app in that namespace. The time
picker controls usage history, peak usage, restart counts, and logs. Status panels
show current pod readiness, backups, and firing or pending alerts.

Apps use the pod's `app.kubernetes.io/name` label, falling back to `app`, `k8s-app`,
then `component`. Original log fields remain intact. Workloads sharing an app label
within a namespace appear together.

CPU displays 100% per core and memory as the container working set. Gauges use the
selected apps’ total configured resource requests as their baseline. Requests are scheduling
reservations, so usage can exceed them. With no request configured, gauges use the
allocatable capacity of the nodes hosting the selected apps. Peak stats show the highest combined usage, sampled
at one-minute resolution and including the current value. Request and limit lines show the configured totals.

Resource usage and requests are added together across the selection. Shared pods
and PVCs are counted once. Logs include entries from any selected app.

Backups are matched through the PVCs mounted by the selected apps and each ReplicationSource's
`sourcePVC`. The dashboard shows the last successful sync, native VolSync status,
and median duration. A successful sync does not establish restorability. Restore
evidence is in [the recovery record](rke2-recovery-verification.md).

Active alerts match the selected apps, their pods, PVCs, and backup sources. The
table shows pending and firing instances.

## Searching logs

In Explore, select **VictoriaLogs** and use LogsQL:

```logsql
kubernetes.pod_namespace:="default" "kubernetes.pod_labels.app.kubernetes.io/name":="minecraft"
```

Add a word or phrase to search the message:

```logsql
"kubernetes.pod_labels.app.kubernetes.io/name":="minecraft" "error"
```

Quoted field names preserve Kubernetes labels containing dots or slashes. See the
[LogsQL reference](https://docs.victoriametrics.com/victorialogs/logsql/).

Vector normalizes severity into searchable `level` fields before ingestion. Use
`level:="warning"` or `level:="error"` to filter. See [log formats and application
fields](../kubernetes/apps/monitoring/victoria-logs/README.md#searchable-fields).

The dashboard, app identity rules, and exporter configuration are provisioned from
Git under `kubernetes/apps/monitoring/victoria-metrics/app`. kube-state-metrics exports
pod labels, resource specifications, PVC relationships, and VolSync sync timestamps.
Kubelet scrapes supply measured usage. Logs use the existing VictoriaLogs datasource.
