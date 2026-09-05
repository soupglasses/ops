<!--
SPDX-FileCopyrightText: 2026 Soup Bot <ops+bot@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# Soupbot GitHub routing

A single GitHub App webhook routes GitHub work to the logical Hermes profiles. The
GitHub actor remains `soupbot`; comments identify the profile and run ID because
Hermes profiles are not separate GitHub accounts.

## Canonical identity

All Hermes profiles use this identity for agent-created commits and copyright headers:

```text
Soup Bot <ops+bot@finnes.dev>
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

A named command is logically addressed directly to that specialist. Because one Hermes
webhook route has one fixed profile binding, the current single-webhook deployment uses
`opslead` only as a thin dispatcher: it launches the exact named profile with the full
issue or pull-request context, waits for it, and returns no competing answer. Opslead
must not redo or reinterpret that specialist's work. A future dedicated transport
router could remove this implementation hop without changing the public command syntax.

## Automatic pull-request routing

Non-draft pull requests route to `opslead` on `opened`, `reopened`, `ready_for_review`,
and `synchronize`. Opslead examines the changed files and existing comments before
routing review:

- `security` is mandatory for RBAC, identity, trust-boundary, network exposure,
  admission, CI permissions, and supply-chain changes.
- `resilience` is mandatory for stateful workloads, PVCs, storage, databases,
  snapshots, VolSync, backups, restores, retention, destructive migrations, and
  image-lifecycle assumptions.
- `observability` handles monitoring, scraping, metrics cardinality, alerting, and
  dashboard changes when specialist review is useful.

An agent cannot approve its own implementation. Repeated webhook deliveries and
already-handled requests should produce no additional comment.

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
- **Events:** select individual events, then enable **Issues**, **Issue comments**, and
  **Pull requests**.
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
`{_hermes.target_profile}`. They must not use a `payload.` prefix. Use `{__raw__}` when
the complete transformed payload is required. Literal `{payload}` or
`{payload.some_field}` text indicates a broken route template.

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
