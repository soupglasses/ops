<!--
SPDX-FileCopyrightText: 2026 Soup Bot <bot+ops@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# Soupbot GitHub routing

A single GitHub App webhook routes GitHub work to the logical Hermes profiles. The
GitHub actor remains `soupbot`; comments identify the profile and run ID because
Hermes profiles are not separate GitHub accounts.

## Canonical identity

All Hermes profiles use this identity for agent-created commits and copyright headers:

```text
Soup Bot <bot+ops@finnes.dev>
```

The display name is exactly `Soup Bot`, with a space. Do not use `SoupBot` as the Git
author name and do not substitute GitHub's generated noreply address. Existing human
authorship is preserved when agents edit existing files.

## Commands

Mention the app in an issue body, pull-request body, issue comment, or pull-request
conversation:

```text
@soupbot
@soupbot opslead
@soupbot incident
@soupbot observability
@soupbot implementer
@soupbot security
@soupbot resilience
```

A bare `@soupbot` routes to `opslead`. Separators such as `:`, `,`, `/`, and `-` are
accepted, as is `@soupbot to security`. Messages authored by `soupbot` are ignored to
prevent response loops.

Only repository collaborators with `write`, `maintain`, or `admin` permission are
trusted. The router checks the triggering sender against GitHub's repository
collaborator-permission API and fails closed if that lookup fails. Outside contributors,
first-time contributors, and read/triage-only users are ignored even when they mention
`@soupbot`.

A named command is logically addressed directly to that specialist. Because one Hermes
webhook route has one fixed profile binding, the current single-webhook deployment uses
`opslead` only as a thin dispatcher: it launches the exact named profile with the full
issue or pull-request context, waits for it, and returns no competing answer. Opslead
must not redo or reinterpret that specialist's work. A future dedicated transport
router could remove this implementation hop without changing the public command syntax.

## Automatic pull-request routing

New non-draft pull requests route to `opslead` on `opened` or `reopened`; drafts route
once on `ready_for_review`. New commits (`synchronize`) do not wake Soup Bot by
themselves. Opslead examines the changed files and existing comments before routing
review:

- `security` is mandatory for RBAC, identity, trust-boundary, network exposure,
  admission, CI permissions, and supply-chain changes.
- `resilience` is mandatory for stateful workloads, PVCs, storage, databases,
  snapshots, VolSync, backups, restores, retention, destructive migrations, and
  image-lifecycle assumptions.
- `observability` handles monitoring, scraping, metrics cardinality, alerting, and
  dashboard changes when specialist review is useful.

An agent cannot approve its own implementation. Repeated webhook deliveries and
already-handled requests should produce no additional comment.

The accepted trigger surface is deliberately narrow:

- a new issue from a trusted collaborator;
- an explicit `@soupbot` ping in a new or edited issue body;
- a new or newly-ready pull request from a trusted collaborator;
- an explicit `@soupbot` ping in a PR body, conversation comment, inline review
  comment, or submitted review body;
- a PR review request explicitly addressed to the Soup Bot account.

All Soup Bot-authored issues, PRs, comments, and reviews are ignored. Routing agents
must respond on the existing issue or PR and must never create another issue or PR as
an acknowledgement, routing record, or response to a webhook event.

The route uses Hermes's `github_comment` delivery. The selected profile returns one
attributed response, and the webhook adapter posts it to the existing issue or PR using
the normalized item number produced by the router. Agents must not post a second copy.

## Labels

Routing uses these label namespaces:

- owner: `agent/opslead`, `agent/incident`, `agent/observability`,
  `agent/implementer`, `agent/security`, `agent/resilience`;
- state: `state/needs-triage`, `state/investigating`, `state/needs-review`,
  `state/changes-requested`, `state/approved`, `state/implementing`,
  `state/verifying`, `state/blocked`;
- type: `type/incident`, `type/monitoring`, `type/security`, `type/reliability`,
  `type/change`;
- severity: `severity/critical`, `severity/high`, `severity/medium`, `severity/low`.

Only one `agent/*` owner label should represent active coordination. Mandatory
specialist reviews are recorded in attributed comments or reviews and do not transfer
ownership unless opslead says so.

## GitHub webhook configuration

Create one repository webhook for `soupglasses/ops`:

- **Payload URL:**
  `https://hermes.finnes.dev/p/opslead/webhooks/github-router`
- **Content type:** `application/json`
- **Secret:** the shared HMAC secret configured in both the GitHub App and the protected
  Hermes `github-router` subscription. Rotate both sides together; do not commit it.
- **SSL verification:** enabled.
- **Events:** select individual events, then enable **Issues**, **Issue comments**,
  **Pull requests**, **Pull request reviews**, and **Pull request review comments**.
- **Active:** enabled.

The reverse proxy must allow public `POST` requests to the exact
`/p/opslead/webhooks/github-router` path while keeping the dashboard private. Do not
open the whole `/p/` namespace. Hermes validates every request using
`X-Hub-Signature-256`, filters the repository and action, and rate-limits duplicate
GitHub deliveries.

The default Hermes gateway must run with `gateway.multiplex_profiles: true`, include
`opslead` and every target profile in `gateway.multiplex_profile_allowlist`, and be
restarted after changing that allowlist. The dynamic `github-router` route is bound to
`opslead`; route definitions hot-reload, but gateway multiplex configuration does not.

Webhook prompt placeholders address the transformed payload directly, for example
`{_hermes.target_profile}`. They must not use a `payload.` prefix. The router emits a
small routing envelope (`item`, `comment` or `review`, and `_hermes`) rather than the
complete GitHub payload: the agent gets authoritative current state with `gh` after it
starts. The prompt starts with the item type, number, and title so generated Hermes
session names identify the actual work. Do not add `{__raw__}` to this route; it makes
prompts and transcripts needlessly large. Literal `{payload}` or `{payload.some_field}`
text indicates a broken template.

The router uses log-only adapter delivery and instructs the agent to post once with
`gh issue comment`, which works for both issues and pull requests. This avoids Hermes'
current `github_comment` adapter path, which invokes `gh pr comment` and cannot deliver
to issues. Keep the independent PR-only resilience route on adapter delivery.

## Verification

After saving the webhook, use GitHub's **Recent deliveries** page to redeliver its
`ping` only to confirm transport. Then create a temporary issue comment containing
`@soupbot opslead ping test`. Confirm that:

1. GitHub reports HTTP 200 for the delivery;
2. gateway logs show `github-router` under the `opslead` profile;
3. opslead posts one attributed response;
4. the response does not trigger a second response;
5. a direct specialist command routes to the named profile;
6. opening a harmless draft PR produces no review, while marking it ready routes it to
   opslead.
