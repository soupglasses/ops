<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# Jellyfin Upjet handoff

Updated 2026-10-18. Resume the remaining cleanup from this document before changing
plugin installation. The application settings migration is implemented; plugin
binary installation remains a separate follow-up.

## Agreed design

- Use the Terraform provider's ownership-fix revision
  [`65dbe330`](https://github.com/soupglasses/terraform-provider-jellyfin/commit/65dbe330d0862501b43c841ab7ed93953096ad3a)
  through Upjet, with no Terraform executable or state volume.
- Install a native OCI Crossplane Provider package. Production runs Crossplane
  2.4.2. Managed APIs and ProviderConfig are namespaced; the runtime watches `media`.
- Accept native Crossplane's standard trusted-controller Secret permissions.
  Namespace-local selectors do not make those permissions namespace-restricted.
- Reuse the existing first-setup `ops-recovery` administrator. Other deployments
  can use `usernameSecretRef`, for example a `JELLYFIN_ADMIN_USERNAME` key. No new
  account or secret is needed for this server.
- Keep desired settings in `kubernetes/apps/media/jellyfin/settings/`.
  PluginSettings composes a generated PluginConfiguration and safely serializes
  Secret-backed fields. Exclude Delete from management policies.
- Preserve PVCs and the operator-requested no-backup policy. Crossplane restores
  declared settings, not lost users, mappings, photos or databases.

## Deployed artifacts and completed gates

Native provider:
`ghcr.io/soupglasses/ops/provider-jellyfin@sha256:df798c6aedf296135b3239666236ab8f33c2157909dceb90a003b11682fa95f0`.
Source: `b34cb7b705eb87f6317c40f0811e4c82275896a7`.
[Publication CI](https://github.com/soupglasses/ops/actions/runs/39091069770) passed.

Slim application runtime:
`ghcr.io/soupglasses/ops/jellyfin-runtime@sha256:eff7a6ebc2609f6b6b2c5322e00971694d778b0d2c6467466b441def4cd39b88`.
Source: `6616498b8e60da44171dc4582c562cc33b5f714b`.
Both images were anonymously pullable. Source and build recipes are in this repo.

1. Published and installed the native package with all settings Observe-only.
2. Fixed generated sensitive Secret selectors: unused initProvider selectors must
   be pointers and omitted. A real Kubernetes REST-client regression now covers
   the empty-name failure that the fake client missed.
3. Upgraded the package, removed the temporary valid initProvider reference, and
   verified strict Ready **and** Synced health for every expected settings resource
   at `1c878626efd2f060e295eaac4720173900c1fd6d`.
4. Retired the configuration sidecar at
   `25259821d937b6acde176cf594c5536691563b97`. The replacement pod is healthy with
   only the Jellyfin container, no desired-settings or credential mounts, and the
   same server identity, version and completed setup. All Observe checks passed
   again after restart. The obsolete ConfigMap left the app's Flux inventory.
5. Enabled Observe/Create/Update at
   `bba29dd84757bb4eb5b31e43b1fdc8a78e07dfff`. Added a temporary legacy ConfigMap
   manifest to transfer that exact unused object into a pruning inventory.
   Its explicit namespace was corrected at
   `2db8596c10f17fc5f91f971167cfcade4079ddbb` after Flux rejected the missing
   namespace. All three Flux Kustomizations subsequently reached Ready/Healthy at
   that exact revision, including strict settings Ready/Synced health. Private
   HTTPS and the unchanged server identity were verified again.

The namespace correction is live: the provider Kustomization is Ready/Healthy,
its inventory contains `media_jellyfin-configuration__ConfigMap`, and the unused
ConfigMap's Flux owner is `jellyfin-provider`. Only its final pruning remains.

Independent security and resilience reviews approved the staged handoff and exact
ConfigMap cleanup. Keep the ordering: adoption, sidecar retirement, then writes.
The configuration sidecar's source, tests, dependencies and obsolete documentation
were removed. The runtime retains plugin seeding and checkpoint utilities.

## Remaining cleanup

1. Confirm `jellyfin`, `jellyfin-provider` and `jellyfin-settings` are Ready/Healthy
   at or after `2db8596`. Confirm settings have Ready and Synced conditions and
   management policies Observe/Create/Update. The current assigned credential
   cannot directly read the generated Jellyfin CRDs; strict Flux health expressions
   cover SystemConfiguration, NetworkingConfiguration, BrandingConfiguration,
   Library and PluginSettings. Do not mistake package Healthy for settings health.
2. Confirm the provider Kustomization inventory contains
   `media_jellyfin-configuration__ConfigMap` and the ConfigMap's Flux owner is
   `jellyfin-provider`. It must have no pod references. App pruning is deliberately
   false; do not enable it globally to remove this object.
3. Remove only `provider/legacy-configuration.yaml` and its entry in
   `provider/kustomization.yaml`. Validate, commit and push. Flux will prune the
   unused ConfigMap through the provider Kustomization's existing `prune: true`.
   Verify NotFound afterward. Leave no temporary cleanup resource.
4. Finalize the rollout entry in `docs/application-reconciliation.md` and the
   application reconciliation checkbox in `TODO.md` against live evidence.
5. Plugin binaries are still seeded by the runtime. Before using Plugin,
   PluginRepository or ScheduledTask for installation, remove competing seeding
   and prove repository ordering, replacement semantics, updates and restart/load
   behavior. An Update-only policy may not support version changes that require
   replacement. This optional work was not started today.

## Verification and access

Use the assigned credential explicitly; mise's default points at forbidden
`.secure/` state. Never read `.secure/`, decrypt secrets, or modify existing SOPS
files. Read-only access cannot patch Flux sources or delete ConfigMaps; let GitOps
perform changes. A manual Flux reconcile was denied, while automatic Git sync works.
Dependency revision propagation can take several minutes after each push.

```sh
export KUBECONFIG=/mnt/home/sofie/Code/github.com/soupglasses/ops/kandev.kubeconfig
mise exec -- env KUBECONFIG="$KUBECONFIG" kubectl -n media get \
  kustomizations jellyfin jellyfin-provider jellyfin-settings
mise exec -- env KUBECONFIG="$KUBECONFIG" kubectl -n media get pods -l app=jellyfin
mise exec -- env KUBECONFIG="$KUBECONFIG" kubectl -n media get \
  configmap jellyfin-configuration \
  -o jsonpath='{.metadata.labels.kustomize\.toolkit\.fluxcd\.io/name}{"\n"}'
mise exec -- env KUBECONFIG="$KUBECONFIG" kubectl -n media get \
  kustomization jellyfin-provider -o jsonpath='{.status.inventory.entries}'
curl --fail --silent --show-error --max-time 10 \
  --resolve jellyfin.home.finnes.dev:443:172.21.68.11 \
  https://jellyfin.home.finnes.dev/System/Info/Public
```

Use fully qualified `providers.pkg.crossplane.io` if inspecting the native package;
unqualified `provider` can select Flux notification resources. The assigned
credential cannot directly read native package resources, but can read pod images,
logs, events, and Flux health/inventory.

Passed checks: generated connector ownership/drift tests, real REST Secret lookup,
real Jellyfin/OpenLDAP identity and checkpoint compatibility fixture, actual
composition rendering with synthetic credentials, and native-package installation
plus real controller observation/update/drift/Secret resolution in kind. The kind
fixture reconnects its startup port-forward if Jellyfin restarts its listener.
Disposable clusters and fixture containers were removed. Latest manifest validation:
57 passed, 1 skipped. Use `task flate:diff OUTPUT=human` because an inherited OUTPUT
variable can otherwise select an invalid output format. Format YAML with yamlfmt.

## Workspace and publication

Implementation worktree: `/tmp/jellyfin-handoff`, branch `feat/jellyfin-handoff`.
Production changes were pushed to `main`. Preserve all unrelated user work.
The original checkout is back on `main`. The older held migration edits were
removed by reverse-applying their exact saved patch after a successful reverse
check, then fast-forwarding main. Unrelated user files remain untouched. Do not
reverse `/tmp/jellyfin-held-handoff.patch` again or reset, clean or stash user files.
Generated Go APIs are ignored and may need regeneration for the pointer selector
change; use the provider README's generation/build workflow.

[Issue 110](https://github.com/soupglasses/ops/issues/110) tracks the migration.
The publication guard rejected a new issue comment. Do not bypass or retry via
another interface. A review-evidence draft is saved at
`/tmp/jellyfin-handoff-review-note.md`; the user can publish it with:

```sh
gh issue comment 110 --repo soupglasses/ops \
  --body-file /tmp/jellyfin-handoff-review-note.md
```

The handoff document and repository commits are the durable continuation record;
temporary logs and worktrees are useful evidence but must not be required to rebuild.
