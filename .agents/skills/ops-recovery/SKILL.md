---
name: ops-recovery
description: Assess or improve application recovery and data survival. Use when explicitly asked to use ops-recovery.
---

<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# Establish recoverability

Aim for usable applications and data after a clean rebuild from Git, off-cluster
backups, and independently held recovery keys. Trace the recovery path affected by
the change, including storage, dependencies, artifact availability, and operator error.

Use the rendered diff and relevant upstream notes to establish what changes in PVC
identity, mounts, ownership, data formats, backup behavior, retention, or restoration.
For routine upgrades with an unchanged recovery contract, focus on migrations,
rollback compatibility, a healthy recent backup, and application smoke testing.

Where recovery behavior changes, establish the evidence needed for that failure path:

- Restore new storage automatically through Flux using the repository's VolSync
  `ReplicationDestination` and PVC `dataSourceRef` pattern for ordinary workloads.
- Use portable dumps, write quiescing, or database-native backup/PITR where application
  consistency requires it. Match backup-reader ownership to the data.
- Keep independently usable history off-cluster for delayed corruption discovery.
  Document recovery endpoints, key requirements, ordering, and artifact rebuild paths.
- Make failed and stale backups visible with actionable signals.
- Exercise the affected restore path and check application contents and new writes.
  Preserve the known-good copy until the replacement is validated.

Choose checks in proportion to the actual change. A successful backup or Ready pod
can support execution evidence; restored contents establish data recovery. A blocking
finding names a concrete path introduced or worsened by this change to data loss or
failed recovery. Record unrelated recovery debt separately.

Use `kubernetes/components/volsync/README.md` for the PVC contract,
`docs/openldap-volsync-migration.md` for a restore-drill example, and
`docs/cluster-bootstrap.md` for clean-cluster recovery. For PR reviews, return a verdict
against the current green head with evidence and the smallest adequate mitigation.
