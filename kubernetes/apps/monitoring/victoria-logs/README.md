<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# VictoriaLogs

VictoriaLogs stores one year of Kubernetes container and node journal logs on a 200 GiB
HDD-backed ZFS PVC. Background merges compact indexed data automatically. Compression
reduces storage use, but does not remove repeated source events. Free-space alerts and
a 5 GB ingestion reserve protect the volume from filling completely.

Vector runs once per node. It reads only `/var/log`, `/run/log/journal`, and the host machine ID required
by `journalctl`, which cover CRI container logs and persistent or volatile systemd journals used by RKE2.
Its writable host path contains only delivery checkpoints and a 512 MiB disk buffer. Vector needs UID 0
to read root-owned journal files,
drops all capabilities, uses the runtime-default seccomp profile, and has a read-only root filesystem.
The VictoriaLogs API is not exposed outside the cluster and a NetworkPolicy limits it to the monitoring
and log-collection namespaces because VictoriaLogs does not implement authorization itself.

Grafana discovers the provisioned `VictoriaLogs` datasource and bundled dashboards by label. The
Grafana deployment must install the signed `victoriametrics-logs-datasource` plugin before startup.

The Vector K8S Monitoring dashboard is kept in `app/vector-k8s-monitoring.json`,
rendered from the [upstream chart 0.12.3](https://github.com/VictoriaMetrics/helm-charts/tree/victoria-logs-single-0.12.3/charts/victoria-logs-single).
Its three event-rate panels use `e/s`. When upgrading the chart, refresh this copy
and retain that unit change. The chart copy is excluded from Grafana discovery.

## Recovery contract

VictoriaLogs intentionally has no backups. Its existing log data remains on the PVC,
but a clean rebuild creates an empty volume and starts collecting new logs. Logs lost
with the node or storage are accepted as disposable operational history.

The Vector checkpoint host path is also not backed up. After node loss, Vector resumes
from available journal and CRI files; entries already rotated off the node are lost.

## Searchable fields

Vector parses JSON, Kubernetes klog, logfmt, and timestamped console logs before
shipping them. Jellyfin, Minecraft and OpenLDAP connection statistics use parsers scoped to
their app labels. Kubernetes labels remain unchanged. Unrecognized text is retained.
The container timestamp remains the event time, including for formats without a date.

`level` is normalized to `critical`, `error`, `warning`, `info`, `debug`, `trace`, or
`unknown`. Explicit `log.level` wins over a top-level JSON `level` or `severity`.
Journal entries use `PRIORITY`. When no recognized severity exists, a message starting
with the standalone word `WARN` or `WARNING` is classified as warning regardless of
capitalization. Words embedded elsewhere in the message do not affect severity. OpenLDAP connection statistics are informational,
and their LDAP result code remains separately searchable as `log.fields.err`.
Other slapd messages without severity remain unknown. Ingress access logs use HTTP
status: 5xx is error, 4xx is warning, and other responses are info unless an explicit
severity is supplied. Their display message combines method, host, path, and status.
Structured events without a message retain their original text as a fallback. Blank
container log lines are discarded.

```logsql
level:="error"
```

```logsql
"kubernetes.pod_labels.app.kubernetes.io/name":="openldap" "log.fields.conn":="1107"
```

```logsql
"kubernetes.pod_labels.app.kubernetes.io/name":="openldap" "log.fields.err":* NOT "log.fields.err":="0"
```

Parsed application fields live under `log`, including `log.logger`, `log.thread`,
and logfmt keys such as `log.module`. Grafana uses the stored `level` for severity.
Jellyfin console lines expose their message, logger, thread and time under `log`;
Serilog severity abbreviations are normalized to the same levels.
Older entries retain their original schema. Parser changes do not replay stored logs.

Parser fixtures are native Vector tests alongside the collector configuration. Run
`vector test` against its rendered configuration when changing formats. Built-in
parsers follow the [Vector VRL reference](https://vector.dev/docs/reference/vrl/functions/).
