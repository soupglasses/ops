<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# Agent cluster feedback loop

Minimal facts a Claude agent needs to test changes against the live cluster.

## Where things live

- This (ops) repo: `~/Code/Codeberg/soupglasses/ops/` — Flux source of truth (branch `main` on `git@codeberg.org:soupglasses/ops.git`).
- Tooling: `mise`. Run commands as `mise exec -- <cmd>` from the repo root, or `mise activate`. Without mise no `kubectl`/`flux`/`kustomize`/`task` exist.
- Cluster API: `https://k8s.home.arpa:6443` — only resolves on home VPN. If `kubectl` fails with `lookup k8s.home.arpa ... no such host`, the user is off-VPN; stop and ask.

## Golden rule

End state is **always** flux-managed. Never leave `kubectl apply`'d resources behind. Push to git → reconcile → observe. Manual apply is only OK if you reliably remove it before the session ends.

## Cycle

```
edit YAML
task flux-local:test     # validate kustomizations + helmreleases
task flux-local:diff     # see rendered diff vs HEAD
git add … && git commit  # pre-commit runs: editorconfig, kubeconform, reuse, yamlfmt
git push                 # → codeberg → flux GitRepository polls
task flux:reconcile      # nudge flux instead of waiting for interval
mise exec -- kubectl -n <ns> get … / logs / describe
```

For new files, license headers are mandatory: `task license:0bsd FILE=path` (or `:mit`).

## Substitution model

Flux runs `postBuild.substitute` on every Kustomization. Global vars come from `ConfigMap/cluster-settings` and `Secret/cluster-secrets` in `flux-system` (wired by `clusters/home/cluster-apps.yaml`). Per-app vars go in the app's `ks.yaml` under `spec.postBuild.substitute:`. Kustomize Components reference these as `${VAR}` / `${VAR:=default}`.

## Useful task targets

- `flux-local:test` — full local validation (use before every commit that touches manifests)
- `flux-local:diff` — rendered diff vs HEAD
- `flux:reconcile` — kick the flux-system Kustomization
- `flux:reconcile-ks` — reconcile every Kustomization
- `flux:unstuck` — same, plus retry stuck ones

## Gotchas

- Chart-created PVC names differ from Kustomize-created ones. The Minecraft chart auto-creates `minecraft-datadir`; switching to volsync-managed PVC means setting `persistence.dataDir.existingClaim: <name>` and disabling chart provisioning.
- `openebs-hostpath` does **not** support CSI snapshots — volsync `copyMethod: Snapshot` requires `openebs-zfs` (or `openebs-zfs-data` for Retain reclaim).
- The user has WIP uncommitted changes in `main` regularly. `git status` first; stage only your own files (`git add <explicit paths>`), never `git add -A`.
- This is Forgejo/Codeberg, not GitHub. `gh` won't work for PRs here; just push to `main` (it's a personal repo).
