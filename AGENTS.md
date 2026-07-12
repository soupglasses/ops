<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# Agent Guidelines

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

## CI/CD (Forgejo Actions)

Workflows are in `.forgejo/workflows/`. This is a **Codeberg/Forgejo** environment, not GitHub.

- Forgejo supports `github.*` context variables for compatibility, but prefer `forge.*` where available
- Docker steps use `uses: docker://image` with `args`
- Runners are `codeberg-tiny` or `codeberg-tiny-lazy`

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

- **NEVER commit changes** - User must review and commit themselves
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

You have **NO ACCESS** to secret values. You can only see:

- Secret names (from `metadata.name`)
- Key names (from `stringData` or `data` keys)
- The encrypted blobs (which are opaque to you)
