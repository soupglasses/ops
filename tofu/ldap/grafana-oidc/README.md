<!--
SPDX-FileCopyrightText: 2026 Soup Bot <bot+ops@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# Grafana identity

This root module registers Grafana as a confidential OpenID Connect client in
Canaille's LDAP backend. It also creates `grafana-admins`, initially containing
only the bootstrap `admin` user. Canaille owns later group membership changes;
OpenTofu deliberately does not reconcile the group's `member` attribute.

The module and its Kubernetes manifests are intentionally not linked from the
active identity Kustomization yet. Grafana initially deploys with one local
SOPS-managed administrator so monitoring is usable while Canaille is recovered.
Enable this integration only after creating both secrets below.
Add the identity Secret to `kubernetes/apps/identity/grafana-oidc/app/kustomization.yaml`,
then add `grafana-oidc/ks.yaml` to the identity app Kustomization. Until then,
the inactive directory still renders cleanly but must not be reconciled.

Grafana requests `openid profile email groups`. Access is restricted to
`grafana-admins`, and its members map to the Grafana server-administrator role.
The exact callback is `https://grafana.finnes.dev/login/generic_oauth`.

## Secret contract

Generate one random OIDC client secret of at least 64 characters and put the
same value in both SOPS-encrypted Kubernetes Secrets:

- Identity: Secret `grafana-oidc-registration`, key `oidc_client_secret`.
- Monitoring: Secret `grafana-auth`, key `oidc_client_secret`.

The monitoring Secret also carries `admin-user` and `admin-password` for a
single local break-glass account. Keep that password independent from Canaille
and store it in the recovery password manager.

The registration Terraform resource consumes the existing
`ldap-admin-credentials` Secret for `ldap_bind_password`; do not copy the LDAP
administrator password into the Grafana secrets.

## Recovery and rotation

On a fresh LDAP directory, restoring both encrypted Secrets from Git lets
OpenTofu register the client with the same credential before Grafana starts.
This avoids depending on Terraform state for credential recovery.

An OpenLDAP data restore also restores this group and client. Restore the
Tofu Controller state Secret `tfstate-default-grafana-oidc` before resuming the
Terraform resource; otherwise its first apply will correctly stop because the
LDAP entries already exist. Never delete a restored client merely to satisfy
empty Terraform state. The state backup/restore format is documented by the
Tofu Controller project and should be included in the cluster recovery drill.

To rotate the client secret, update both encrypted Secrets to the same new value
in one commit. Reconcile the registration first and VictoriaMetrics second.
OIDC may be unavailable during that short handoff, so verify the local account
before rotating. Afterward, complete a Canaille login and confirm that a user
outside `grafana-admins` is rejected.
