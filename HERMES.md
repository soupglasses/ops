<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# Hermes Agents and Review Requests

General repository guidance is in [AGENTS.md](AGENTS.md).

## Identity and Routing


GitHub Issues and pull requests are the durable coordination record. The profile names
and identity rules in this section apply only to agents actually launched by Hermes.
Those agents use the shared `soupbot` GitHub identity, so each Hermes-authored comment
or review must state its logical profile and Hermes run/session identifier.

Codex, humans, and other external agents are not Hermes profiles. They must not claim a
Hermes profile, invent a Hermes run ID, use the Soup Bot identity, or follow a profile's
runtime instructions merely because their work resembles that profile's role. They
still follow this repository's review gates: request Hermes help with a collaborator-
authored `@soupbot <profile>` command and identify only their actual runtime when useful
(for example, `Requested by Codex session /root; not a Hermes run`). The responding
Hermes specialist supplies its own profile and run/session attribution.

A bare `@soupbot` mention routes to `opslead`. A named command such as
`@soupbot security` is addressed to that specialist; the current single-webhook
implementation uses opslead as a thin dispatcher because a Hermes webhook route is
bound to one profile, but opslead must not redo or reinterpret the specialist's work.
Read `docs/soupbot-github-routing.md` for accepted commands, automatic PR routing,
labels, and webhook operations.

- `opslead`: triage, deduplication, decomposition, routing, and closure criteria.
- `incident`: evidence-first outage diagnosis and root-cause analysis.
- `observability`: scrape design, cardinality, alerts, recording rules, and dashboards.
- `implementer`: scoped GitOps implementation and live verification.
- `security`: broad security judgment across architecture, identity, workloads, networks,
  CI/CD, secrets, supply chain, abuse cases, and operational interactions.
- `resilience`: broad resilience judgment across availability, data survival, recovery,
  dependencies, failure domains, capacity, rollout/rollback, and operator error.

## Delegation

Delegation is durable through GitHub. Before an internal agent is dispatched, the
existing issue or PR records the focused task, expected deliverable, and ownership. A
full handoff replaces the single active `agent/*` owner label; a bounded consultation or
specialist PR review leaves ownership unchanged and records the requested review. Hermes
delegation or Kanban is execution transport, not the visible source of work state. The
delegate posts its evidence and outcome to the same item, after which the coordinator
updates state and ownership. Labels do not themselves invoke profiles, and Soup
Bot-authored comments remain ignored by the webhook to prevent loops, so the coordinator
must explicitly dispatch after writing the durable GitHub handoff.

