<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
SPDX-License-Identifier: MIT
-->

# LDAP provider development

This package embeds the Terraform LDAP SDK through Upjet. It does not invoke
Terraform/OpenTofu or use a state directory. LDAP DNs identify external resources.

## Source and generated code

| Path | Ownership |
| --- | --- |
| `config/provider.go` | Maintained Upjet configuration and SDK sensitivity rules |
| `cmd/generate/` and `generate.sh` | Maintained generation pipeline |
| `cmd/provider/` | Maintained startup, connection handling and namespace boundaries |
| `apis/namespaced/v1beta1/providerconfig_types.go` | Maintained provider configuration API |
| `config/schema.json` | Generated SDK schema snapshot, checked into Git |
| `../../charts/ldap-provider/crds/` | Generated CRDs and license sidecars, checked into Git |
| Other generated Go API/controller/method files | Produced inside the Docker build; not checked into Git |
| `../../charts/ldap-provider/templates/` and `files/` | Maintained Helm and Crossplane composition source |

GitHub marks the committed generated files through `.gitattributes`. Keep their
upstream licenses and regenerate them instead of editing their contents. In
particular, the provider configuration Go types and composition templates are
maintained source even though they produce Kubernetes APIs.

From the repository root:

```sh
task ldap:generate
task ldap:export-generated OUTPUT=/tmp/ldap-generated-source
```

`generate` refreshes the schema snapshot and CRDs using pinned Docker tools.
`export-generated` exports generated Go files plus the SDK schema, with a
`MANIFEST` of their original paths. It excludes maintained provider types,
connection code and composition templates. The Docker target is
`generated-source`; exporting it does not copy the Go toolchain or module cache.
CI checks that both committed schema outputs reproduce exactly.

## Typed identities

`Entry` is generated from the LDAP SDK. `OAuth2Client` and `GroupOfNames` are
maintained XRD/composition definitions in the chart. Crossplane generates their
live CRDs and composes ordinary Entries and attribute Secrets. These generated
runtime resources have owner references and identity UID labels; change their
parent CR rather than editing the child resources.

`GroupOfNames` follows Canaille's LDAP model: `cn`, `member`, `owner` and
`description`. It owns the object class and description. `initialMembers` and
optional `initialOwners` apply only to new LDAP entries; later Canaille edits
are preserved. LDAP maintains reverse `memberOf` through its existing overlays.
Deleting the CR removes composed Kubernetes objects while retaining the LDAP
group. A new CR with the same `cn` adopts it without replacing membership or UUID.

The class requires at least one member when creating an entry. Do not insert an
implicit administrator or sentinel to hide that requirement. App-specific group
names and bootstrap membership belong with each application's manifests.

References: [Canaille LDAP setup](https://canaille.readthedocs.io/en/latest/howtos/database/ldap.html),
[deployed Canaille group model](https://github.com/yaal-coop/canaille/blob/0.3.6/canaille/backends/ldap/models/core.py#L100-L109).

## Tests

The chart's production `helm test` is read-only. Disposable kind integration
exercises the real provider and compositions, including membership/owner
preservation, validation, drift repair and retained-resource re-adoption. See
[chart operations](../../charts/ldap-provider/README.md).
