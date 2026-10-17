---
name: ops-deliver
description: Deliver a requested GitOps change in this repository. Use when explicitly asked to use ops-deliver.
---

<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# Deliver a GitOps change

Aim for a verified application change with a maintainable Git history. Resolve the
request against current repository and cluster evidence, then implement the smallest
complete change using established manifests and task commands.

Use a branch and worktree for concurrent work. Inspect the affected task definitions
and tests before editing. Validate the behavior that changes, and inspect the rendered
Flux diff for effects hidden by chart values or Kustomize patches.

For GitHub work, keep progress and evidence on the existing issue or PR. A useful
handoff names the focused question, expected result, and owner. Coordination should
help resolve the work; choose specialist input when it answers a consequential open
question. Routine changes can proceed on their own evidence.

Follow required CI to completion with a blocking CLI wait, then assess the current
head and rendered diff. Address failures and return an actual verdict for review
requests. Separate regressions from unrelated debt and suggest the smallest adequate
fix for each material finding.

When the task includes shipping, carry the change through merge, Flux reconciliation,
and an application smoke test. Report the deployed revision and observed behavior.
For branch-only work, leave a reviewable diff and report the checks performed.
