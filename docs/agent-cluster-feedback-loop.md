<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# Agent cluster feedback loop

Minimal facts a Claude agent needs to test changes against the live cluster.

## Where things live

- This (ops) repo: `~/Code/codeberg.org/soupglasses/ops/` — Flux source of truth
  (branch `main` on `git@github.com:soupglasses/ops.git`).
- Tooling: `mise`. Run commands as `mise exec -- <cmd>` from the repo root, or `mise activate`. Without mise no `kubectl`/`flux`/`kustomize`/`task` exist.
- Cluster API: `https://k8s.home.finnes.dev:6443` — upstream DNS resolves to the private node address; routing requires the home network or VPN. After a clean rebuild, the human operator refreshes credentials with `task kube:kubeconfig`.

## Golden rule

End state is **always** flux-managed. Never leave `kubectl apply`'d resources behind. Push to git → reconcile → observe. Manual apply is only OK if you reliably remove it before the session ends.

## Cycle

```
edit YAML
task flate:test     # validate kustomizations + helmreleases
task flate:diff     # see rendered diff vs HEAD
git add …                        # explicit paths only
mise exec -- git commit …        # pre-commit hooks need mise's PATH (ec, kubeconform, …)
git push                         # → GitHub → flux GitRepository polls
task flux:reconcile      # nudge flux instead of waiting for interval
mise exec -- kubectl -n <ns> get … / logs / describe
```

For new files, license headers are mandatory: `task license:0bsd FILE=path` (or `:mit`).

## Substitution model

Flux runs `postBuild.substitute` on every Kustomization. Global vars come from `ConfigMap/cluster-settings` and `Secret/cluster-secrets` in `flux-system` (wired by `clusters/home/kustomize.yaml`). Per-app vars go in the app's `kustomize.yaml` under `spec.postBuild.substitute:`. Kustomize Components reference these as `${VAR}` / `${VAR:=default}`.

## Useful task targets

- `flate:test` — full local validation (use before every commit that touches manifests)
- `flate:diff` — rendered diff vs HEAD
- `flux:reconcile` — kick the flux-system Kustomization
- `flux:reconcile-ks` — reconcile every Kustomization
- `flux:unstuck` — same, plus retry stuck ones

## Gotchas

- **Quoted-numeric substitutions become ints.** `value: "${PORT:=25575}"` looks safe but kustomize re-emits the placeholder *without* outer quotes (it's already non-numeric thanks to `$`/`{`/`:`), so after Flux substitutes `25575` you end up with `value: 25575` and SSA dry-run rejects it (`expected string, got Value:25575`). Fix: force the type tag, e.g. `value: !!str ${PORT:=25575}`, or read the value via `valueFrom: configMapKeyRef`. Hardcoding is the laziest workaround and loses configurability.


- Chart-created PVC names differ from Kustomize-created ones. The Minecraft chart auto-creates `minecraft-datadir`; switching to volsync-managed PVC means setting `persistence.dataDir.existingClaim: <name>` and disabling chart provisioning.
- `openebs-hostpath` does **not** support CSI snapshots — volsync `copyMethod: Snapshot` requires `openebs-zfs` (or `openebs-zfs-data` for Retain reclaim).
- The user has WIP uncommitted changes in `main` regularly. `git status` first; stage only your own files (`git add <explicit paths>`), never `git add -A`.
- This is GitHub. Direct pushes to `main` deploy through Flux; use `gh` for pull requests
  and Actions inspection.
