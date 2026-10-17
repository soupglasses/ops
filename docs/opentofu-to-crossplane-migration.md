<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
SPDX-License-Identifier: MIT
-->

# OpenTofu retirement plan

Replace OpenTofu execution and state with the existing Crossplane Upjet LDAP
provider. Preserve the restored directory, existing DNs, passwords, memberships
and application identities. This is a migration plan; no ownership transfer has
been performed yet.

The target has no Tofu Controller, Terraform CRs, runner workloads, tfctl tooling
or `tofu/` modules. Upjet continues using the existing LDAP Terraform provider SDK
as a Go library. It does not invoke OpenTofu or need a state directory. No new
controller, provider API or foundation CRD is needed.

## Verified scope

Inventory on 2026-10-17 found one live Terraform CR:
`identity/ldap-system-users`, using `./tofu/ldap/base`. It has no output Secret and
already sets `destroyResourcesOnDeletion: false`. Its state Secret is
`identity/tfstate-default-ldap-system-users`; its plan Secret is
`identity/tfplan-default-ldap-system-users`. Only names, keys and ownership metadata
were inspected, not their contents.

| Source | Current use | Replacement |
| --- | --- | --- |
| `tofu/ldap/base` | Live; 14 LDAP entries | Existing Upjet `Entry` resources in `identity` |
| `tofu/ldap/service-account` and `components/ldap-account` | No active consumer | Existing app-scoped `Entry` pattern, as used by Jellyfin |
| `tofu/ldap/grafana-oidc` and its Flux manifests | Deliberately not enabled | Remove dormant Tofu scaffolding; keep an inactive `OAuth2Client` example if useful |
| `tofu/ldap/app-identity` and `components/app-identity` | Untracked draft work | Preserve the draft's intent before retiring it; do not silently delete user work |

Do not enable Grafana OIDC, generate credentials or change Grafana login behavior
as part of retirement. A future activation uses the existing `OAuth2Client`
composition and its own credential setup. It is not a live migration blocker.

The 14 foundation entries are nine OUs (`users`, `system`, `groups`, `policies`,
`oauth`, and `clients`, `authorizations`, `consents`, `tokens` under `oauth`), two
password policies, `uid=admin`, `uid=auth`, and `cn=admin,ou=groups`. Consent,
authorization and token records inside those OUs remain runtime data, not Git
resources. Canaille currently binds using the root DN, not the legacy `uid=auth`;
preserve both account DNs without changing that privilege model in this migration.

## Target structure and credentials

Keep the Flux Kustomization name `ldap-system-users` so Canaille's dependency
continues to identify the same foundation. Replace its application manifests with
an identity-local `ProviderConfig`, an attribute Secret, and the 14 Entries.

The local provider references the existing `identity/openldap` Secret's
`LDAP_ROOT_PASSWORD`. It supports the legacy DNs without changing them. Other
application namespaces keep the guarded `ClusterProviderConfig/directory`, with
namespace opt-in, namespace-prefixed RDNs and allowed-parent checks unchanged.

```yaml
apiVersion: ldap.ops.finnes.dev/v1beta1
kind: ProviderConfig
metadata:
  name: directory-admin
  namespace: identity
spec:
  host: openldap.identity.svc.cluster.local
  port: 636
  tls: true
  tlsServerName: ldap.finnes.dev
  bindDN: cn=admin,dc=finnes,dc=dev
  credentialsSecretRef:
    name: openldap
    key: LDAP_ROOT_PASSWORD
---
apiVersion: ldap.ops.finnes.dev/v1alpha1
kind: Entry
metadata:
  name: directory-users
  namespace: identity
  annotations:
    crossplane.io/external-name: ou=users,dc=finnes,dc=dev
spec:
  managementPolicies: [Observe]
  providerConfigRef:
    kind: ProviderConfig
    name: directory-admin
  forProvider:
    dataJsonSecretRef:
      name: directory-foundation-attributes
      key: users.json
    restrictAttributes: [objectClass]
```

These are adoption examples, not generated CRD edits. Use `dataJsonCreateDefaultsSecretRef`
for bootstrap values, then enable `Create` and `Update` only after the checks below.
Do not enable `Delete` for foundation entries.

The existing `ldap-admin-credentials` Secret already contains both bootstrap hash
keys. Flux can substitute references to those single-line RFC2307 hashes into a
new ordinary Secret's JSON values inside the cluster. The provider consumes the
result through its existing Secret references. Existing encrypted files remain
unchanged, and no agent reads, copies or rotates credential values.

Two rendering details must be proved before adoption:

- The global patch in `kubernetes/clusters/home/cluster-apps.yaml` replaces child
  `postBuild.substituteFrom` lists. Append the foundation's `ldap-admin-credentials`
  reference with a narrowly targeted patch after the global patch. The final
  render must retain `cluster-settings`, `cluster-secrets` and the LDAP reference.
- `optional: false` checks that the Secret exists, not that each key exists.
  Validate required keys and nonempty RFC2307 values without printing them before
  enabling creation. Undefined Flux substitutions otherwise become empty strings.
  Do not assume `${key:?}` is supported or enable a global feature gate without
  checking its effect on other workloads. This interpolation is for these hashes,
  not arbitrary passwords or client secrets requiring JSON escaping.

Keep the existing SOPS Secret under Flux ownership when replacing the old
application manifests. There must be no interval where pruning the old stack can
delete a required credential Secret.

## Attribute ownership

| Entry | Continuously owned | Create-only or human-owned |
| --- | --- | --- |
| Nine OUs | Required schema attributes | Unrelated directory attributes |
| Password policies | Explicit policy settings | Unlisted operational attributes |
| Bootstrap admin user | Required schema attributes | Initial password, profile, photos and later human edits |
| Legacy auth user | Required schema and policy association | Existing password; no rotation during adoption |
| Admin group | Required schema attributes | Initial members only; preserve current members thereafter |

The existing Tofu admin-group declaration owns membership, and its user resources
own bootstrap password/profile values. Do not mechanically copy those ownership
rules: they could remove human-added administrators or reset a changed password.
Make these preservation choices explicit in the migration diff and tests.
OpenLDAP server ACLs remain native LDIF in the OpenLDAP deployment, not ordinary
LDAP Entry resources. Jellyfin's existing Entries and login behavior stay intact.

## Adoption and retirement order

1. **Establish the rollback point.** Verify a fresh native LDAP backup and an
   isolated restore. Preserve the current state artifact through an approved,
   encrypted off-cluster backup mechanism without exposing its values. Record
   immutable source/image revisions and the minimal existing recovery keys.

2. **Freeze the old owner in Git.** Set the Terraform CR's `suspend: true` and
   explicitly retain `destroyResourcesOnDeletion: false`. Wait for an already
   running reconciliation/runner to finish; suspension is not cancellation.
   Keep the controller, source artifact and old module available during handoff.

3. **Adopt exact DNs in observe-only mode.** Replace the foundation manifests in
   stages while retaining the suspended Terraform CR and credential Secret.
   Order the foundation after `ldap-directory`/the provider. Confirm all 14
   Entries identify the existing objects and cannot write in this phase. Do not
   rename users, groups, policies or clients to satisfy the tenant prefix rule.

4. **Enable the new owner.** Compare the intended attribute subset without
   revealing credentials. Confirm current password values, memberships, photos
   and runtime OAuth records are unchanged. Enable `[Observe, Create, Update]`
   with explicit attribute whitelists and create-only defaults. Make Canaille's
   existing foundation dependency wait for the new Entry health checks rather
   than the old Terraform condition. Avoid a dependency cycle: provider depends
   on OpenLDAP, foundation depends on provider, Canaille depends on foundation.

5. **Finalize the old CR while its controller still exists.** Verify there is
   still no `writeOutputsToSecret`; otherwise preserve/rehome its Secret and
   consumers first. Request deletion with destruction disabled, then unsuspend
   only the terminating object if required for finalization. Do not resume an
   ordinary active apply or strip finalizers to force removal. Preserve the old
   source until the finalizer and any runner finish; the following deployment
   stage removes the controller and modules.

6. **Remove the obsolete system.** Delete the controller HelmRelease and Flux
   Kustomization, Tofu HelmRepository, stale runner resources, and the Terraform
   CRD only after there are no CRs in any namespace. Remove all tracked `tofu/`
   modules, dormant Tofu application/component scaffolding, obsolete dependency
   and health-check references, and `.mise.toml`'s tfctl tool/completion hooks.
   Retain the shared `GitRepository/flux-system`. Keep generic state-file ignore
   patterns to prevent accidental credential commits. Convert useful examples
   to the existing Crossplane APIs; reconcile untracked drafts deliberately.

7. **Prove recovery without Tofu.** In a disposable cluster, restore LDAP and
   existing credentials, then install Crossplane/provider and recreate resources
   from Git with no tfstate. Verify existing identities, Canaille administration,
   LDAP password login and Jellyfin photos. Test Grafana OIDC only if it was
   separately enabled; local Grafana login must remain usable. Keep the encrypted
   rollback archive until this drill and the live handoff pass.

### Controller deletion behavior

In Tofu Controller 0.16.5, disabling external resource destruction does not
preserve output Secrets: finalization explicitly deletes them and generated
outputs also have Terraform owner references. The live base CR currently has no
output Secret, but recheck before deletion.

Suspended CRs return before deletion handling, so deleting a still-suspended CR
can leave its finalizer waiting forever. Complete adoption first, then allow
normal finalization only for the terminating retained-resource CR. The original
module and controller must remain available until that finishes.

Sources: [controller suspension handling](https://github.com/flux-iac/tofu-controller/blob/v0.16.5/controllers/tf_controller.go#L189),
[finalizer](https://github.com/flux-iac/tofu-controller/blob/v0.16.5/controllers/tf_controller_finalizer.go),
[Secret cleanup](https://github.com/flux-iac/tofu-controller/blob/v0.16.5/runner/server.go#L524),
[output ownership](https://github.com/flux-iac/tofu-controller/blob/v0.16.5/runner/server_outputs.go#L81).

## Verification and completion

Extend the existing provider's disposable kind/Helm integration tests rather than
adding a test controller. Exercise adoption of a prepopulated directory, unchanged
passwords and human membership/photos, controller restart without state, and
retained LDAP objects when managed resources are removed. Smoke `helm test` in
production remains read-only. Missing bootstrap inputs must prevent the migration
from enabling creation; tests must never print credentials.

Run `task flate:test` and inspect the rendered diff for dependency ordering,
Secret references and attribute ownership. After each staged commit/push,
reconcile Flux and verify LDAP binds, Canaille and Jellyfin through real APIs.
Obtain independent security and resilience approval against the completed diff
and restore evidence before retiring the last rollback path.

The final inventory must show zero Terraform CRs, controller/runner workloads,
Tofu-specific RBAC and active state/plan Secrets; no tracked `tofu/` files; and no
active references to removed modules or tooling. Upjet's SDK dependency and
historical migration references are intentional exceptions. Record the clean
recovery evidence and close the migration TODO only after the live checks pass.
