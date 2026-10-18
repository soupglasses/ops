<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
SPDX-License-Identifier: MIT
-->

# LDAP reconciliation chart

The controller uses Upjet with the MIT-licensed Terraform LDAP provider's SDK directly.
It does not run Terraform or keep a Terraform state directory. LDAP distinguished
names in `crossplane.io/external-name` identify entries after controller restarts.
`Observe`, `Create` and `Update` policies retain directory data when a CR is removed.

`Entry` is the generic namespaced API. Its attribute JSON documents are Kubernetes
Secret references because they can contain password hashes or OAuth credentials.
`restrictAttributes` names the attributes owned by Git; unlisted human-managed
membership and binary photographs remain untouched. `dataJsonCreateDefaultsSecretRef`
supplies bootstrap-only values without reconciling them afterward.

`OAuth2Client` is a backend-neutral namespaced API, currently composed into the
Canaille LDAP schema. Applications reference a local credential Secret and the
cluster-admin-owned `ClusterProviderConfig` named `directory`. Its namespace must
opt in with `identity.ops.finnes.dev/backend: directory`. Central credentials stay
in the identity namespace. Managed DNs must be direct children of the configured
bases and their RDN value must start with the application's namespace plus a dot.
A local `ProviderConfig` is available for directories with their own credentials.

`GroupOfNames` is a typed LDAP group API. Set `groups.enabled=true` to install
its XRD and composition. It can be enabled independently of OAuth2; both use the
same composition function. Because the LDAP API is installed through Helm, the
chart grants Crossplane access to Entries through one aggregated ClusterRole.
Crossplane already has permission to manage the generated Secrets.

```yaml
apiVersion: identity.ops.finnes.dev/v1alpha1
kind: GroupOfNames
metadata:
  name: example-users
spec:
  backendRef:
    kind: ProviderConfig
    name: directory-admin
  cn: example.users
  description: Users permitted to access Example
```

Membership and owners are managed only in Canaille. The platform uses the
non-login account at `groups.sentinelDN` to satisfy LDAP's required `member`
attribute when creating a group. Provision this account before group resources;
the directory foundation does this in production. Keep it in otherwise empty
groups so the last human member can be removed without violating LDAP's schema.
No human account receives access or ownership from the group declaration.
Existing groups are adopted without changing members or owners. Keep the group CR
with its application's Kubernetes configuration; it need not be stored in the
provider installation. An administrative ProviderConfig can be reused within
its namespace. Tenant-facing ClusterProviderConfig restrictions remain enforced.

Generated CRDs retain their upstream Apache license. From the repository root:

```sh
task ldap:generate
task ldap:export-generated DESTINATION=/tmp/ldap-generated-source
```

See [provider development](../../packages/provider-ldap/README.md) for the source
inventory, generated-source export and schema reproducibility checks.

Do not edit generated files or add license headers to their contents. Generation
emits REUSE sidecars. A CRD upgrade uses Flux HelmRelease `CreateReplace`; removal
of the release does not delete Helm's separately installed CRDs.

Tests default to disabled. Enable `tests.enabled=true` for read-only `helm test`
acceptance. `tests.mode=integration` creates synthetic directory data, rotates
credentials and restarts the controller; use it only in a disposable kind cluster.
The test executable is shared by the Docker and Helm interfaces.
