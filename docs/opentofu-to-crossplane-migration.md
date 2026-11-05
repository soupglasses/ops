<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
SPDX-License-Identifier: MIT
-->

# Directory ownership and OpenTofu retirement

Crossplane's existing Upjet LDAP provider owns the directory foundation. OpenTofu
execution, state, controller, runners, tooling and `tofu/` modules are retired.
Upjet embeds the LDAP Terraform provider SDK as a Go library; it does not invoke
OpenTofu or require a state directory. Generated provider files remain generated.

## Configuration

The Flux Kustomization `identity/ldap-system-users` keeps its name so Canaille's
existing dependency remains valid. Its application directory contains:

- `providerconfig.yaml`: identity-local `ProviderConfig/directory-admin`, verified
  LDAPS, using the existing `identity/openldap` Secret's `LDAP_ROOT_PASSWORD`.
- `entries.yaml`: 14 entries adopted by their existing DNs, with
  `[Observe, Create, Update]` management policies. Deleting these Kubernetes
  resources retains their LDAP objects because `Delete` is excluded.
- `attributes.yaml`: attribute documents referenced by those entries.
- The unchanged encrypted bootstrap credential Secret.

The entries are nine OUs, two password policies, `uid=admin`, `uid=auth`, and the
administrator group. Authorization, consent and token records remain runtime data.
Canaille still binds using the existing root DN; reducing that privilege is a
separate task.

Reconciliation owns declared schema and password-policy attributes. Passwords,
human names, email addresses and administrator membership are create-only defaults.
Existing passwords, JPEG photos, profiles and members survive adoption and later
reconciliation. Administrative membership changes belong in Canaille.

Flux substitutes the existing `ldap-admin-credentials` bootstrap hash keys into
the new attribute Secret inside the cluster. A targeted patch appends that Secret
after the global substitution-source patch; it preserves `cluster-settings` and
`cluster-secrets`. These are single-line RFC2307 hashes, not arbitrary JSON input.
Existing encrypted files were not changed or decrypted. Missing bootstrap inputs
must be detected before enabling creation; `optional: false` checks the Secret's
existence, not individual keys.

Application namespaces continue using the guarded
`ClusterProviderConfig/directory`, with namespace opt-in, allowed parent DNs and
namespace-prefixed RDNs. Grafana uses the existing `OAuth2Client` composition in
`monitoring`, its own encrypted client credential, PKCE and signed ID-token
validation. Only `monitoring.grafana-admins` can sign in through OIDC; the existing
local recovery administrator remains available. See
[Grafana sign-in](../kubernetes/apps/monitoring/victoria-metrics/identity/README.md).

## Recovery

Restore OpenLDAP through the existing VolSync destination and PVC `dataSourceRef`
contract before applying the provider and directory entries. The restored DNs are
adopted again from Git; no Terraform state import is needed. Create-only defaults
are used only if an entry is absent. Never rebuild an empty directory over the
only surviving data copy.

Recovery requires the repository, the existing SOPS age key, off-cluster backup
credentials and endpoint, and the pinned application/provider images or their
recorded rebuild paths. These credentials are not recorded here. Follow
[OpenLDAP recovery](openldap-volsync-migration.md),
[native directory exports](openldap-nfrastack-migration.md), and the
[VolSync contract](../kubernetes/components/volsync/README.md) for ordering,
retention and selecting an older known-good recovery point.

The old state and plan were archived as age ciphertext at
`/data/migrations/opentofu-20261017/state-plan.json.age`, inside the backed-up LDAP
volume. Its recipient is the existing recovery age key; no additional key was
created. This archive is optional historical rollback material, not a dependency
of Crossplane recovery. Never resume the old membership-owning module against a
live directory without first stopping Crossplane writes and reviewing its plan.

## Migration and restore evidence, 2026-10-17

The only live Terraform CR was `identity/ldap-system-users`, with 14 entries and
no output Secret. The controller was first suspended, and Crossplane adopted all
14 DNs in observe-only mode. An internal disposable check confirmed both rendered
bootstrap hashes exactly matched the existing source values and were nonempty
RFC2307 inputs, without displaying them.

A membership change occurred during observation. It was preserved as current
human state rather than replaced with historical defaults. Immediately before
enabling create/update, a fresh baseline was captured. All 14 managed entries
became Ready, and the complete native directory export remained byte-identical:

`d3bc21aea43ff6482949c36897cf5a037a80a4d8e87a463b670aea93a0047796`

The off-cluster backup completed at 19:41:00 UTC, snapshot `ce0e68e8`. An isolated
restore passed at 19:41:47 UTC. The restored export exactly matched both the paired
native backup and that handoff baseline: 91 entries, including five password-bearing
entries, two photos, six membership values and the existing OAuth client. The UUID
set hash was `620912f651360c9246ef033c9ab7a06417d297889eab31c620a10f9a9f779726`.
The restored database remained unchanged through two native policy-init passes.
This backup precedes the new Grafana client and group; those are recreated from Git.

The encrypted rollback archive also survived both the pre-migration and
post-migration restore drills, with SHA256:

`cd7a61e2dca5a6abe3701c975fdd73d9ea330a8bcd5d6d80d9c3f7a4919b84db`

The real-provider kind/Helm regression
`TestFoundationAdoptionPreservesRestoredIdentity` passed. It proves observe-only
entryCSN preservation, schema reconciliation without changing passwords,
profiles, JPEGs, membership or UUIDs, and deletion/recreation of managed resources
with stable LDAP identities and no state import. It runs through the existing
Helm integration hook; production Helm tests remain read-only smoke checks.
Temporary test and restore resources were deleted, and scheduled backups remain
configured.

Final inventory confirmed the controller, runners, Terraform CRD, active state and
plan Secrets, generated runner RBAC and unused Tofu package source were removed.
The tracked and working-tree `tofu/` directory is gone. Untracked unused draft
modules were archived and moved outside the repository before removing the folder.
No bootstrap credential Secret or generated provider file was changed.

After adoption, Flux removed the Terraform manifest. The terminating CR's exact
UID, deletion timestamp, disabled destruction and absent output Secret were
checked before clearing suspension to let its finalizer finish. The controller
and modules remained available until the CR was gone. This ordering matters:
Tofu Controller 0.16.5 skips deletion handling while suspended, and disabling LDAP
destruction does not preserve generated output Secrets.

Sources: [controller suspension handling](https://github.com/flux-iac/tofu-controller/blob/v0.16.5/controllers/tf_controller.go#L189),
[finalizer](https://github.com/flux-iac/tofu-controller/blob/v0.16.5/controllers/tf_controller_finalizer.go),
[Secret cleanup](https://github.com/flux-iac/tofu-controller/blob/v0.16.5/runner/server.go#L524),
[output ownership](https://github.com/flux-iac/tofu-controller/blob/v0.16.5/runner/server_outputs.go#L81).
