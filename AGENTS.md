<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# Working in ops

Git and Flux own durable production state. Work in an isolated worktree, preserve
unrelated changes, and stage explicit paths.

## Desired end state

- Services must converge automatically from an empty installation to their declared
  state using Git, external backups and the minimum documented recovery credentials.
  Prove fresh installation and recovery, including configuration and usable data.
- Prefer typed, declarative APIs for application settings and identities. Reuse
  Terraform providers through Crossplane Upjet, compositions and CRDs where useful.
  Use Components, templating and native Helm tests to share platform behavior.
  Keep implementation workarounds inside the platform, out of application declarations.
- Apply file-based configuration at startup. Make Flux changes take effect through
  native reloads or workload rollouts without manual intervention. Use generated
  ConfigMap names or equivalent change tracking when a restart is needed.
- Use the shared LDAP identities and SSO where supported. Applications must not
  maintain independent human account lists. A first-configuration or recovery
  administrator is an explicit exception. Prefer directory-backed service identities.
- Restore backed-up state automatically from S3 through VolSync when local data is
  absent. Block startup until recovery and required image pulls complete. Declare
  intentional fresh initialization and cost-versus-rebuild exceptions explicitly:
  Jellyfin configuration and media have no backups because rebuilding is cheaper.
- Pin deployable artifacts and automate updates with Renovate. Validate behavior
  through the real application interfaces, not duplicated versions or snapshots
  of today's deployment.

## Documentation and follow-up work

Keep durable usage and ownership guidance in the relevant README. Reserve `docs/`
for material such as architecture and disaster recovery that needs a separate home.
Track unfinished work and operational evidence in GitHub issues. Do not grow TODO.md
into a change log. Remove completed entries and obsolete migration notes.

Do not duplicate pins, commit hashes, resource inventories or current environment
snapshots in maintained prose or tests. Ordinary dependency and environment updates
should not require editing unrelated documentation or incidental assertions.

## Delivery and verification

- Use `mise exec -- ...` for tools managed by the repository's Mise configuration.
  Run other commands, including `git` and `gh`, directly.
- For manifest changes, run `task flate:test` and inspect `task flate:diff` before
  shipping. Use `BASE=origin/main` when comparing a branch. These tasks align local
  rendering with CI, including CRDs and the Kubernetes version.
- Format changed YAML with `yamlfmt`. Pass the configured pre-commit checks, and
  preserve SPDX notices. Match neighboring licenses for new files using
  `task license:0bsd FILE=...` or `task license:mit FILE=...`.
- For pull requests, wait with `gh pr checks <n> --watch --interval 30`, then fetch
  the current head, results, comments, and Flux Local diff. Base the verdict on that
  head and evidence relevant to the change.
- Push production changes to `main`, reconcile with `task flux:reconcile`, and verify
  the affected application live. Keep branch preparation distinct from deployment.
- Remove temporary verification resources and leave the end state Flux-managed.
  Record actionable follow-up findings in GitHub issues.

## Data and access

Backed-up application data must survive a clean rebuild from Git, off-cluster backups,
and independently held recovery keys. For changes affecting recovery, preserve a rollback
copy until restored application contents have been validated. Use the
[VolSync contract](kubernetes/components/volsync/README.md) and
[restore-drill example](docs/openldap-volsync-migration.md) for recovery changes.
Use native database recovery where a volume copy cannot provide consistency.

Use the credentials assigned to the current runtime. Mise defaults `KUBECONFIG` to
`.secure/kubeconfig`; when using an externally supplied credential, select it after
mise loads the project environment:
`mise exec -- env KUBECONFIG=<assigned-path> kubectl ...`.

Keep `.secure/` and its contents unread. Existing `.sops.yaml` files are user-managed:
if one needs editing, ask the user to make the change and wait for confirmation.
Never decrypt, rotate, view plaintext, or copy values from existing secrets. A new
secret may be encrypted exactly once with
`sops --encrypt --in-place <new-file>.sops.yaml`; this is the only permitted SOPS
operation. User-edited secrets may be committed after confirming their values remain
encrypted. Secret names, key names, and opaque encrypted blobs may be inspected.

## CI and optional skills

Pin GitHub Actions to full commit digests. Keep workflow permissions explicit and
restrict write operations to trusted contexts.

Project skills live in `.agents/skills/`: `ops-deliver`, `ops-incident`,
`ops-observability`, `ops-security`, and `ops-recovery`. Their descriptions determine
when to select them. Each can also be invoked with `$<name>`. Specialist reviews are
available when requested.
