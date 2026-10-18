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
task ldap:export-generated DESTINATION=/tmp/ldap-generated-source
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

`GroupOfNames` declares `cn` and `description`. Git owns the object class and
description; Canaille owns members and owners. New groups contain only the
platform's non-login sentinel account, configured through `groups.sentinelDN`.
This satisfies LDAP's required `member` attribute without granting human access.
Keep the sentinel when removing the last human member in Canaille. The directory
foundation continuously reconciles its unusable password marker.

LDAP maintains reverse `memberOf` through its existing overlays. Deleting the CR
removes composed Kubernetes objects while retaining the LDAP group. A new CR
with the same `cn` adopts it without replacing membership, owners or UUID.
Application manifests contain no membership or ownership lists.

References: [Canaille LDAP setup](https://canaille.readthedocs.io/en/latest/howtos/database/ldap.html),
[deployed Canaille group model](https://github.com/yaal-coop/canaille/blob/0.3.6/canaille/backends/ldap/models/core.py#L100-L109).

## Tests

The chart's production `helm test` is read-only. Disposable kind integration
exercises the real provider and compositions, including membership/owner
preservation, validation, drift repair and retained-resource re-adoption. See
[chart operations](../../charts/ldap-provider/README.md).
