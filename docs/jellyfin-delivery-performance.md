<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# Jellyfin delivery performance

The migration's slow feedback came from repeated cold Go builds and Flux dependency
retry waits. The application and provider workflows already run concurrently.

## Measured CI costs

| Provider run | Native build | kind | Composition | Total job |
| --- | ---: | ---: | ---: | ---: |
| [39091811913](https://github.com/soupglasses/ops/actions/runs/39091811913) | 173s | 146s | 8s | 345s |
| [39091069770](https://github.com/soupglasses/ops/actions/runs/39091069770) | 261s | 145s | 7s | 434s |
| [39092729354](https://github.com/soupglasses/ops/actions/runs/39092729354) | 185s | 143s | failed after 7s | 353s |

Tool installation took approximately 10-15 seconds; publication took four seconds.
Schema reproduction reused the same-job Docker build cache and took zero to one
second. The separate [application run](https://github.com/soupglasses/ops/actions/runs/39091069780)
spent 245 seconds building the generated provider integration driver, 21 seconds
building Jellyfin/plugins, and 37 seconds exercising LDAP identity behavior.

The provider workflow now runs composition rendering first. Settings, composition
and legacy-cleanup edits skip unchanged native builds, kind tests and publication.
Provider source/workflow changes retain the full checks; runtime configuration and
RBAC changes retain native-package kind verification. Provider-only changes still
run real LDAP integration, but do not publish duplicate application images.

## Flux propagation

For `9e004cf`, the production controller finished Crossplane at 02:29:21, the LDAP
provider at 02:30:17, directory at 02:30:21, groups at 02:30:51, identity at 02:31:21,
Jellyfin at 02:31:47, and settings at 02:32:17. Actual reconciliations took roughly
0.2-0.43 seconds. Logs between them reported dependency retries after 30 seconds.

The controller now uses `--requeue-dependency=5s`, preserving all dependency edges
and health checks. Git source polling remains one minute; normal application
intervals remain unchanged. Blocked dependency checks may run up to six times as
often, increasing controller/cache/status/event work while dependencies are blocked.
This does not apply every workload six times as often. Watch controller readiness,
restarts, error logs and resource use after rollout.

## Further improvements

1. Add persistent BuildKit cache across runs, with distinct scopes for provider and
   application builds. Preserve local image loading for xpkg embedding and Docker
   integration tests. The runtime, tests and generated-output targets need a shared
   explicit Buildx builder; adding a cache flag to the existing classic invocation
   is insufficient. Measure warm-run improvement before restructuring jobs.
2. Overlap kind bootstrap/image pulling with compilation, joining before package
   installation. Keep cleanup reliable and avoid competing heavy compilers on a
   small runner.
3. Consider sharing the generated provider build between workflows only after
   measuring artifact transfer costs. Independent jobs already overlap wall time.
4. Batch related manifest edits where no live safety gate separates them. Retain
   separate adoption, writer retirement and write-enablement gates for migrations.

Do not remove genuine dependencies or shorten every application interval to fix
dependency propagation. If credentials allow manual reconciliation, target the
changed chain rather than serially reconciling every application.

References: [Flux dependency retry option](https://fluxcd.io/flux/components/kustomize/options/),
[source-triggered reconciliation](https://fluxcd.io/flux/components/kustomize/kustomizations/),
and [Docker's GitHub Actions cache guidance](https://docs.docker.com/build/cache/backends/gha/).
