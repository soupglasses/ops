<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# CLAUDE.md

Single-node Talos Kubernetes cluster (`nas`), fully managed by Flux from this repo on
Codeberg (Forgejo, not GitHub).

## Values

- Git is the cluster. Every change ends as committed YAML that Flux applies. kubectl is
  for observing and debugging; if a fix happened by hand, it is not done until it is in
  the repo.
- The machine is disposable, the data is not. The node can be wiped and rebuilt from
  this repo plus the restic backups, in the right order, without heroics. Every change
  must keep that true: stateful apps get the volsync component, restores stay automatic
  (ReplicationDestination + PVC dataSourceRef), and nothing may depend on state that
  only exists in the cluster.
- Backups are the one thing that must never silently rot. Anything touching
  `kubernetes/components/volsync*`, storage classes, snapshot classes, or
  `cluster-secrets` is high-risk; slow down and double-check.
- Validate before handing over: `task flux-local:test` must pass, and pre-commit gates
  formatting, schemas, and SPDX headers.
- Secrets are sops-encrypted with age. New secrets: write plaintext as `*.sops.yaml`,
  encrypt in place exactly once. Existing secrets: never decrypt, edit, or rotate; stop
  and ask. Never read `.secure/`.
- Never commit or push. Sofie reviews and commits herself; WIP lives on `main`.

## Ground rules

- `kubernetes/apps/<namespace>/<app>/` uses the two-level pattern: `ks.yaml` plus an
  `app/` folder with no kustomization.yaml inside.
- `kubernetes/clusters/home/flux-system/` is Flux bootstrap output. Do not touch.
- Deeper conventions live in DEVELOPMENT.md, agent workflow rules in AGENTS.md, design
  notes and postmortems in `docs/`.
