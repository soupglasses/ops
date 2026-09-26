<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# Node overview

Open [Node overview](https://grafana.finnes.dev/d/node-overview) and choose a node.
The dashboard uses the existing host node-exporter, kubelet, and kube-state-metrics
scrapes. Its ConfigMap is managed with the monitoring manifests in Git.

The top CPU value shows utilization of the whole node. In the stacked CPU history,
100% means one core. App stacks are grouped by namespace and app, with a separate
node-total line. Pods without an app label remain in an `(unlabelled)` group within
their namespace. The app labels follow the Application overview dashboard.

Memory stacks show container working sets. The host line shows total RAM minus
available RAM, including processes outside Kubernetes. These measurements account
for reclaimable cache differently, so their difference is not an exact measure of
host overhead.

Storage panels show physical disk traffic and busy time, exported filesystem
capacity, and ZFS pool state. Network panels show host interfaces while excluding
pod veths and Cilium’s internal devices to avoid repeating the same traffic.

Use [Application overview](https://grafana.finnes.dev/d/application-overview) to
inspect individual apps, their logs, backups, and alerts.
