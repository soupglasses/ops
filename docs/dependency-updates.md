<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# Dependency updates

Renovate owns the pins in `.renovaterc.json5`. Flux applies merged application
changes. Node upgrades remain explicit maintenance through `task ansible:upgrade`.

| Dependency | Update contract |
|---|---|
| RKE2 | Version and Linux amd64 archive SHA256 change together using the published release checksum attachment. Numeric `rke2rN` revisions are ordered as builds. |
| Cilium | Remains bundled with RKE2. Kubeconform reads the selected release's chart metadata to use its exact Cilium schema. |
| Gateway API | All six CRD URLs carry the same release tag and immutable commit. Renovate updates both parts together. |
| VictoriaMetrics, VictoriaLogs, Minecraft | Runtime and restore guard use the same pinned image. Each application's chart and image updates share a proposal. |
| Vector | A coupled regex updates its separate tag and digest fields. The chart also uses the tag as a Kubernetes label, so it must remain a plain version. |
| OpenEBS | Explicit image repository and `tag@sha256` let Renovate update the version and digest together with its chart. |
| Backup watcher | The current `latest` image is pinned by digest. Renovate proposes digest changes rather than allowing an unreviewed image change on restart. |
| Validation tools and actions | Mise and GitHub Actions managers track Flate, tools, action digests and the Mise runtime. Krew pins use their upstream release annotations. Automerge requires successful checks. |
| Ansible and ordinary workloads | Native pip, Ansible Galaxy, Flux, Helm-values and Kubernetes managers track requirements, charts and images, including both OpenLDAP container references. |
| Grafana VictoriaLogs plugin | Its installed plugin version follows the upstream datasource release in the VictoriaMetrics proposal. |

The RKE2/Gateway API group requires Dependency Dashboard approval before a proposal
is created, and never automerges. Review the selected RKE2 release's bundled
Cilium against the proposed Gateway API version, including optional TLSRoute CRDs.
Grouping keeps changes visible together, but cannot establish upstream compatibility.
Keep Kubernetes upgrades sequential and follow the backup and recovery checks in
[RKE2 operations](rke2-operations.md).

Application groups keep chart changes and image overrides visible together. Inspect
the rendered diff and release notes before merging, including schema and storage
changes. Minecraft's game version, Fabric and mods remain a coordinated application
upgrade, separate from the server container image.

Generated and historical artifacts have separate maintenance paths:

- `kubernetes/clusters/*/flux-system/` is excluded from Renovate. Upgrade Flux by
  regenerating `gotk-components.yaml` with the selected Flux CLI's
  `flux install --export`, preserving the existing components and reviewing the
  resulting RBAC/CRD changes. `task flux:install` and `task flux:bootstrap` apply the
  committed manifests, they do not select a newer controller release.
- `vector-k8s-monitoring.json` is a reviewed, vendored dashboard. When updating the
  VictoriaLogs chart, compare its upstream dashboard with this copy and preserve
  the documented query and display changes.
- Recovery drill versions, source commits and dated evidence describe the tested
  recovery point. Preserve that history and record new artifact recovery evidence
  when advancing images. Examples and historical references are not executable pins.
- Pre-commit updates receive reviewed PRs rather than automerging, following the
  earlier rollback. Run the repository hooks before merging. Encrypted secret files
  remain excluded.

Validate changes with `task flate:test` and inspect `task flate:diff BASE=origin/main`.
For Renovate configuration changes, run `renovate-config-validator .renovaterc.json5`
and a local `renovate --platform=local --dry-run=extract` with a supported Node runtime.
Extraction does not require cluster credentials. GitHub release lookups need the
Renovate application's normal GitHub authentication.

References: [release attachment checksums](https://docs.renovatebot.com/modules/datasource/github-release-attachments/),
[tag commit digests](https://docs.renovatebot.com/modules/datasource/github-tags/),
[Mise support](https://docs.renovatebot.com/modules/manager/mise/).
