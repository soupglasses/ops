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

Generated CRDs retain their upstream Apache license. Regenerate them from source:

```sh
docker build --target generated --output type=local,dest=/tmp/ldap-generated packages/provider-ldap
cp /tmp/ldap-generated/generated/crds/* charts/ldap-provider/crds/
```

Do not edit generated files or add license headers to their contents. Generation
emits REUSE sidecars. A CRD upgrade uses Flux HelmRelease `CreateReplace`; removal
of the release does not delete Helm's separately installed CRDs.

Tests default to disabled. Enable `tests.enabled=true` for read-only `helm test`
acceptance. `tests.mode=integration` creates synthetic directory data, rotates
credentials and restarts the controller; use it only in a disposable kind cluster.
The test executable is shared by the Docker and Helm interfaces.
