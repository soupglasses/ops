<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# components/ldap-account

Per-app LDAP service account, created by tofu-controller and delivered to the app as a
Secret. Full design: `docs/ldap-service-accounts.md`.

Each include stamps one Terraform CR with its own state, owning exactly one entry
(`uid=${APP},ou=system`). Apps never share an account and a broken plan for one app
cannot touch another's.

## Resources

| File | Purpose |
|---|---|
| `terraform.yaml` | Terraform CR `${APP}-ldap` running `tofu/ldap/service-account`; writes outputs to Secret `${APP}-ldap-credentials`. |
| `secret.yaml` | Admin bind password for the runner, templated from `${SECRET_LDAP_ADMIN_PASSWORD}` in cluster-secrets. |

## Consuming app contract

Include the component and give the app's `ks.yaml` ordering plus a healthCheck:

```yaml
# app/kustomization.yaml
components:
  - ../../../../components/ldap-account

# ks.yaml
dependsOn:
  - {name: openldap, namespace: identity}
  - {name: ldap-system-users, namespace: identity}
  - {name: tofu-controller, namespace: flux-system}
healthChecks:
  - apiVersion: infra.contrib.fluxcd.io/v1alpha2
    kind: Terraform
    name: <app>-ldap
    namespace: <app namespace>
```

The app consumes Secret `${APP}-ldap-credentials` (keys `LDAP_BIND_DN`,
`LDAP_BIND_PASSWORD`, `LDAP_URI`, `LDAP_BASE_DN`), e.g. via `envFrom`. The pod cannot
start before the account exists, and the Kustomization is not Ready until the plan has
applied.

## Prerequisites

- `SECRET_LDAP_ADMIN_PASSWORD` present in
  `kubernetes/components/common/config/cluster-secrets.sops.yaml` (same value as the
  `ldap_bind_password` in `ldap-admin-credentials`).
- The app's namespace listed in `runner.serviceAccount.allowedNamespaces` in the
  tofu-controller HelmRelease, with network reachability runner -> source-controller
  (see `docs/tofu-controller-grpc-hang.md`).
- `tofu/ldap/base` applied (it owns `ou=system` and the `cn=system` password policy).
