---
name: ops-security
description: Assess a change for concrete security risks. Use when explicitly asked to use ops-security.
---

<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# Assess security impact

Understand the requested behavior and the trust boundaries it changes. Inspect the
current diff, rendered resources, and relevant upstream behavior. Consider identity,
permissions, secrets, network exposure, workload privilege, admission, CI authority,
and executable supply-chain trust where they intersect the change.

For a changed boundary, identify who gains which capability over which resources.
Relate the permission or exposure to a real operation, consider a narrower solution,
and establish how access can be revoked or the change rolled back. Validate the
important allowed and denied behavior using the smallest useful check.

Rank findings by realistic exploitability and consequence. Each blocking finding
needs evidence and a credible path from this change to harm. Treat unchanged debt
as follow-up unless the change materially amplifies it. Routine upgrades that preserve
the boundary usually need provenance and focused behavior checks.

For a PR, assess the current green head and completed Flux Local diff. Return an
explicit verdict, supporting evidence, and minimal fixes for material findings. Keep
implementation self-checks distinct from an independent review. Use only the access
available to the reviewing runtime.
