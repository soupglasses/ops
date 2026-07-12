<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# Development Guide

Repository-specific conventions for this GitOps codebase.

## Tooling

All tooling is managed by [mise](https://mise.jdx.dev/) (see `.mise.toml`). Run `mise install` to set up the environment.

### Formatting & Linting

| Tool | Purpose | Config |
|------|---------|--------|
| `yamlfmt` | YAML formatter | `.yamlfmt` (max 100 char lines, retains line breaks) |
| `editorconfig-checker` (`ec`) | Enforces `.editorconfig` rules | `.editorconfig` |
| `kubeconform` | Kubernetes manifest validation | Inline args in pre-commit |
| `reuse` | SPDX license header compliance | `.reuse/` |

All YAML files are formatted with `yamlfmt`. Run it before committing:
```bash
yamlfmt file.yaml        # format a file
yamlfmt -lint file.yaml   # check without modifying
```

### Pre-Commit Hooks

Pre-commit runs automatically on commit. Hooks (in order):
1. `editorconfig-checker` — whitespace, line endings, charset
2. `kubeconform` — validates `kubernetes/**/*.yaml` against schemas
3. `reuse` — SPDX license headers on all files
4. `yamlfmt` — YAML formatting
5. `forbid-secrets` — prevents unencrypted `.sops.yaml` files

Install hooks:
```bash
pre-commit install
```

### License Headers

Every file must have SPDX license headers. Use the task commands:
```bash
task license:mit FILE=path/to/file   # MIT license
task license:0bsd FILE=path/to/file  # 0BSD license
```

## Repository Structure

```
kubernetes/
  apps/                    # Applications organized by namespace
    {namespace}/
      kustomization.yaml   # Flux Kustomization (no app/ prefix in resources)
      {app}/
        ks.yaml            # Flux Kustomization resource
        app/               # Application manifests
          helmrelease.yaml
          secret.sops.yaml
          prometheusrule.yaml
          ...
  components/              # Shared Kustomize components
    common/               # Applied to all apps (alerts, namespace, config)
    volsync/              # Backup replication templates
  clusters/               # Flux cluster configurations
    home/                 # Cluster-specific configs
talos/                    # Talos Linux machine configs
.taskfiles/              # Task (taskfile.dev) automation
```

## Key Conventions

### Flux Kustomization Pattern

Apps use **two-level Kustomization**:

1. **`{app}/ks.yaml`** - Flux Kustomization resource that points to `app/` folder
2. **Parent `kustomization.yaml`** - Lists `ks.yaml` in resources (not `app/` directly)

```yaml
# apps/default/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: default
components:
  - ../../components/common
resources:
  - ./minecraft/ks.yaml  # Reference ks.yaml, not minecraft/app/
```

### Kustomization Auto-Generation

Flux auto-generates kustomization from directory contents. **Do not create `kustomization.yaml` in `app/` folders.** Place files directly in `app/` and Flux handles the rest.

### Secret Management

**SOPS encryption required** for secrets containing credentials:

- Files ending in `.sops.yaml` are encrypted
- Use `sops -e -i file.yaml` then rename to `*.sops.yaml`
- Never commit plaintext secrets with actual values

**Cluster-wide secrets** via `cluster-secrets.sops.yaml`:

- Shared credentials use `SECRET_` prefix variables
- Referenced via Flux postBuild substitution: `${SECRET_VAR_NAME}`
- Available cluster-wide through common component

### Component System

**Always apply `common` component** in namespace kustomizations:

```yaml
components:
  - ../../components/common  # Provides namespace, alerts, settings
```

**Optional components** (volsync, privileged) are app-specific.

### Variable Substitution

Flux postBuild substitutes from `cluster-settings` ConfigMap and `cluster-secrets` Secret:

```yaml
# In manifests
stringData:
  value: ${SECRET_DOMAIN}
  endpoint: ${SECRET_S3_ENDPOINT}/bucket
```

Bash-style defaults work: `${VAR:-default}`

### HelmRelease Conventions

- Use `interval: 30m` for reconciliation (`10m` on Flux Kustomizations)
- Enable retries: `retries: -1` for install, `retries: 3` for upgrade with rollback
- Prefer `chartRef` with OCIRepository over HelmRepository
- Use `cleanupOnFail: true` for upgrades

### Health Checks

Include in ks.yaml for HelmReleases:

```yaml
healthChecks:
  - apiVersion: helm.toolkit.fluxcd.io/v2
    kind: HelmRelease
    name: *app
    namespace: *namespace
```

### PrometheusRule Location

Place in `app/` folder alongside HelmRelease.

## Common Patterns

### Adding a New Application

1. Create `apps/{namespace}/{app}/app/` folder structure
2. Add manifests (helmrelease.yaml, secret.sops.yaml if needed, etc.)
3. Create `ks.yaml` in `{app}/` folder
4. Reference `ks.yaml` in parent namespace `kustomization.yaml`
5. Add `common` component to namespace kustomization if not present

### VolSync Backup Setup

1. Add volsync component to app's resources (if not using central template)
2. Create bucket path config via ConfigMap or accept default (`${APP}`)
3. Store S3 credentials in `cluster-secrets.sops.yaml` with `SECRET_` prefix
4. Reference via `${SECRET_S3_ENDPOINT}/volsync-${APP}` pattern

### Prometheus Alerts

- Use `severity: warning` for non-critical issues (like backup failures)
- Use `for:` duration to avoid false positives (e.g., `6h` for transient issues)
- Target specific containers: `container="mc-backup"`
- Include app label: `labels: app: {appname}`

## Validation

Validate Flux configurations locally with [flux-local](https://github.com/allenporter/flux-local):

```bash
task flux-local:test    # Validate all Kustomizations and HelmReleases
task flux-local:diff    # Show rendered manifest diff against HEAD
task flux-local:build   # Build and count resources
```

Pull requests automatically run `flux-local test` and `flux-local diff` in CI.

## Important Notes

- **No manual kustomization.yaml in app/ folders** - Flux auto-generates
- **Always encrypt secrets with SOPS** before committing
- **Use cluster-secrets for shared credentials** to avoid duplication
- **Reference ks.yaml not app/ folder** in parent kustomizations
- **Components use ${APP} variable** substituted from Kustomization name
- **Run `task flux-local:test` before committing** to catch validation errors early
