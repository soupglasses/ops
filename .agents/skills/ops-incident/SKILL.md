---
name: ops-incident
description: Investigate an operational incident and verify a remedy. Use when explicitly asked to use ops-incident.
---

<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# Resolve an incident

Establish user impact, affected services, and the timeline. Collect enough status,
events, and logs to distinguish likely causes. Keep facts, inference, and unknowns
clear in the incident record.

Form a falsifiable hypothesis and choose the least invasive test that separates it
from competing explanations. Preserve evidence before changing a failing system.
Restarts and deletion need a causal reason and a way to assess the result.

Prioritize service restoration according to impact and recoverability. Put durable
remediation in Git and Flux, verify the original symptom through the affected
interface, and check for recurrence. When emergency intervention is necessary, record
what changed and reconcile the intended state back into Git.

Close with the observed cause, remediation, and verification. Track remaining
uncertainty or prevention work separately, in proportion to the incident's impact.
Consult `docs/rke2-operations.md` for cluster operations and application recovery.
