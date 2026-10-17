---
name: ops-observability
description: Improve operational metrics, alerts, probes, or dashboards. Use when explicitly asked to use ops-observability.
---

<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# Build useful observability

Start with the operational question and the decision a signal will change. Query
current data and inspect existing discovery, rules, and dashboards before adding
telemetry. Reuse signals that already answer the question.

Prefer native metrics and maintained exporters. Use Kubernetes state and events or
synthetic probes for gaps they directly address. Introduce custom exporters or
log-derived metrics when simpler sources cannot answer the question reliably.

Estimate series cardinality, scrape volume, retention, and storage impact. Bound
labels using observed values, and remove unbounded dimensions at ingestion. Give
alerts an actionable response and a runbook; tune thresholds and duration against
history so transient conditions do not dominate paging.

Verify discovery and actual samples, evaluate queries against representative data,
and exercise the alert or probe condition where practical. Report which operational
question is now answered and any known blind spots. Dashboard polish and unrelated
coverage gaps belong in follow-up work.
