<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# Application overview

Open **Application overview** in Grafana and select a namespace and app. The time
picker controls usage history, peak usage, restart counts, and logs. Status panels
show current pod readiness, backups, and firing or pending alerts.

Apps use the pod's `app.kubernetes.io/name` label, falling back to `app`, `k8s-app`,
then `component`. Original log fields remain intact. Workloads sharing an app label
within a namespace appear together.

CPU is measured in cores and memory as the container working set. Gauge capacity is
the sum of container limits when every container has a limit, otherwise the
allocatable capacity of the nodes hosting the app. Peaks are sampled at one-minute
resolution. Request and limit lines show the configured totals.

Backups are matched through the PVCs mounted by the app and each ReplicationSource's
`sourcePVC`. The dashboard shows the last successful sync, native VolSync status,
and median duration. A successful sync does not establish restorability. Restore
evidence is in [the recovery record](rke2-recovery-verification.md).

Alerts match the app, its pods, PVCs, and backup sources. Alerts without those labels
remain in the cluster alert view. No matching alerts does not imply that every
possible application failure has an alert rule.

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

The dashboard, app identity rules, and exporter configuration are provisioned from
Git under `kubernetes/apps/monitoring/victoria-metrics/app`. kube-state-metrics exports
pod labels, resource specifications, PVC relationships, and VolSync sync timestamps.
Kubelet scrapes supply measured usage. Logs use the existing VictoriaLogs datasource.
