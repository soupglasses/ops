<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# Agent Guidelines

## Working Loop

Commits and pushes to `main` are how changes deploy; you are expected to do both and
then verify the result live. The loop:

1. Edit manifests, then `task flux-local:test` (must pass).
2. Commit with `mise exec -- git commit ...` (pre-commit hooks need mise's PATH to find
   `ec` and friends). Small logical commits, conventional style (`feat(scope):`,
   `fix(scope):`), written in a human voice.
3. Stage explicit paths only; the human keeps WIP on `main`, so never `git add -A`.
4. Push, `task flux:reconcile` (or `flux reconcile ks <name> -n <ns> --with-source`),
   and observe with kubectl until the change demonstrably works. Retry and fix forward;
   do not stop at "committed".
5. Backup or restore changes additionally get a restore drill: pull the data back out
   and check the contents (see docs/openldap-volsync-migration.md for the pattern).
6. Throwaway verification resources (test pods, one-off CRs) must be deleted before
   finishing. End state is always Flux-managed.
7. Record new findings in TODO.md and tick off what you complete.

## Data Survival Is a Merge Gate

The machine and cluster are disposable; application data is not. A clean recovery must
work from this repository, off-cluster backups, and the minimum documented recovery
keys. No required state may exist only in the live cluster, on one node, in an agent's
workspace, or in an image registry with no recovery plan.

Request review from the `resilience` Hermes profile for every change that adds,
removes, or materially changes any of the following:

- a PVC, StatefulSet, storage class, VolumeSnapshot, or reclaim policy;
- a database, datastore, migration, restore script, retention policy, or backup job;
- VolSync resources, quiesce hooks, native dumps, pgBackRest, or equivalent tooling;
- destructive pruning, volume replacement, application reinstallation, or image/chart
  lifecycle assumptions.

A stateful change is not complete until the review establishes all applicable items:

1. **Automatic rebuild:** deleting and recreating the application or PVC through Flux
   restores usable data without undocumented manual state. For ordinary PVC workloads,
   use the repository's VolSync component and its `ReplicationDestination` plus PVC
   `dataSourceRef` pattern.
2. **Corruption recovery:** an operator can select an older known-good recovery point.
   Databases that need point-in-time recovery use a database-native mechanism such as
   pgBackRest (or a justified equivalent); a crash-consistent volume snapshot alone is
   not sufficient.
3. **Application consistency:** quiesce writes or produce a portable native dump before
   snapshots where the workload requires it. Match mover UID/GID so backup jobs can
   actually read the data.
4. **Independent survival:** backups are off-cluster and survive node, ZFS pool, and
   clean-cluster loss. Document the minimal keys, credentials, endpoints, and ordering
   needed to recover them. Never commit those credentials.
5. **Retention and history:** keep enough independently usable recovery points to
   survive delayed corruption discovery. Do not silently reduce retention or make the
   writer credential capable of erasing the only copy without explicit review.
6. **Observable failure:** alert on failed and stale backups without noisy per-run
   paging. A green controller condition proves execution, not restorability.
7. **Proven restore:** run a restore drill and validate application-level contents, not
   only PVC binding, pod readiness, or backup-controller status. Record evidence in the
   issue or PR.
8. **Safe destruction:** never delete or overwrite the only known-good local copy before
   the replacement has been restored and validated. Preserve a rollback point through
   migrations.
9. **Artifact recovery:** required container images and charts must remain fetchable
   after a clean rebuild, or have a documented mirror, digest, source, and rebuild path.

The reviewer must request changes when recovery evidence is missing. "The backup job
succeeded" and "the pod became Ready" are not acceptable substitutes for a restore
test. See `kubernetes/components/volsync/README.md` for the standard PVC contract and
`docs/openldap-volsync-migration.md` for a migration and restore-drill pattern.

## Agent Review Routing

GitHub Issues and pull requests are the durable coordination record. All profiles use
the shared `soupbot` GitHub identity, so each agent comment or review must state its
logical profile and Hermes run/session identifier. A bare `@soupbot` mention routes to
`opslead`. A named command such as `@soupbot security` is addressed to that specialist;
the current single-webhook implementation uses opslead as a thin dispatcher because a
Hermes webhook route is bound to one profile, but opslead must not redo or reinterpret
the specialist's work. Read `docs/soupbot-github-routing.md` for accepted commands,
automatic PR routing, labels, and webhook operations.

- `opslead`: triage, deduplication, decomposition, routing, and closure criteria.
- `incident`: evidence-first outage diagnosis and root-cause analysis.
- `observability`: scrape design, cardinality, alerts, recording rules, and dashboards.
- `implementer`: scoped GitOps implementation and live verification.
- `security`: least-privilege, workload, network, CI, and supply-chain review.
- `resilience`: mandatory independent recovery review for stateful changes.

An agent must not approve its own implementation. RBAC or trust-boundary changes need
`security` review. Stateful or destructive lifecycle changes need `resilience`
review. Changes requiring both need both reviews; one specialty does not substitute for
the other.

## Formatting & Validation

### YAML Formatting

All YAML files MUST be formatted with `yamlfmt` before presenting changes to the user. Run:
```bash
yamlfmt file.yaml
```

Config is in `.yamlfmt`: max 100 char lines, retains line breaks, excludes `clusters/**/gotk-*.yaml`.

### Pre-Commit Hooks

Pre-commit hooks run on commit. The hooks are: `editorconfig-checker`, `kubeconform`, `reuse`, `yamlfmt`, and `forbid-secrets`. If you create or modify files, ensure they pass these checks.

### License Headers

Every file requires SPDX license headers. Use:
```bash
task license:mit FILE=path/to/file   # For MIT
task license:0bsd FILE=path/to/file  # For 0BSD
```

Check existing files in the same directory to match the correct license.
For files created by any Hermes profile, use
`Soup Bot <bot+ops@finnes.dev>` as the copyright holder;
never attribute agent-created files to Sofie or copy her personal identity from a
neighboring header. Preserve existing copyright lines when editing existing files.

### Kubernetes Manifest Validation

Run `flux-local test` to validate Flux Kustomizations and HelmReleases:
```bash
task flux-local:test
```

Run `flux-local diff` to see rendered manifest changes:
```bash
task flux-local:diff
```

**Important**: flux-local emits `SubstituteReference` warnings due to a traversal order limitation. These are cosmetic — do not attempt to fix them. They are filtered from task command output.

**Do NOT use** `--path-orig` or `--branch-orig` flags with flux-local in task commands. These bypass flux-local's internal git worktree creation and break ConfigMap resolution for `substituteFrom` references.

## CI/CD (GitHub Actions)

Workflows are in `.github/workflows/` and run on GitHub-hosted runners.

- Pin actions to full commit digests.
- Keep workflow permissions explicit and avoid write operations for fork-originated pull requests.

## Creating New Encrypted Secrets (.sops.yaml)

### Permitted Workflow (One-Time Only)

When creating a **NEW** secret file:

1. **Write the file** with plaintext values and `.sops.yaml` extension:

   ```yaml
   # secret.sops.yaml
   apiVersion: v1
   kind: Secret
   metadata:
     name: my-secret
   stringData:
     KEY: "plaintext-value"
   ```

2. **Encrypt exactly once** using ONLY this command:

   ```bash
   sops --encrypt --in-place secret.sops.yaml
   ```

### Critical Restrictions

**NEVER do any of the following:**

- Modify an existing `.sops.yaml` file (encrypted or not)
- Use `sops --decrypt` on any file
- Use `sops -d` (decrypt) in any form
- Use `sops --rotate` or `sops -r`
- Use `sops` with any flags other than `--encrypt --in-place`
- Edit or view decrypted secret contents
- Copy secret values from one file to another
- **NEVER READ** .secure OR FILES WITHIN WITH _ANY_ COMMAND OR HELPER TOOL.

### Existing Secrets

**If you need changes to secrets that already exist:**

1. **STOP your work immediately**
2. **Ask the user** to make the changes
3. **Wait for confirmation** before continuing

When the user has edited a sops file themselves, you may commit it for them: first
confirm it is encrypted (key names and `ENC[AES256_GCM` markers are visible; values
must never be), then commit it like any other change.

You have **NO ACCESS** to secret values. You can only see:

- Secret names (from `metadata.name`)
- Key names (from `stringData` or `data` keys)
- The encrypted blobs (which are opaque to you)
