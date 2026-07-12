<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# LDAP Terraform Provider Rewrite

Notes towards a redesigned `terraform-provider-ldap`, motivated by two problems
hit while bootstrapping Canaille against OpenLDAP via `tofu/ldap-system-users/`.

## Problem 1: `data_json` is awkward

Every entry in the existing `l-with/ldap` provider is written as:

```hcl
resource "ldap_entry" "admin_group" {
  dn = "cn=admin,ou=groups,${local.ldap_suffix}"
  data_json = jsonencode({
    objectClass = ["groupOfNames"]
    member      = [ldap_entry.admin_user.dn]
  })
}
```

The `data_json = jsonencode({...})` pattern is a workaround, not a design
choice. Three things stack:

1. **LDAP entries are open-ended.** Any entry can carry any subset of
   attributes from any objectClass, including custom schemas
   (`oathHOTPToken`, `pwdPolicySubentry`, etc.). There is no fixed shape to
   declare in a Terraform schema.
2. **Each attribute is multi-valued.** The natural shape is
   `map[string][]string` — a map from attribute name to list of values.
3. **Terraform Plugin SDKv2 cannot represent `map[string][]string`.** `TypeMap`
   only accepts scalar element types; nested collections aren't supported, and
   there is no `DynamicPseudoType`. So the provider stuffs everything into a
   single string field and asks HCL to compose JSON.

The newer **Terraform Plugin Framework** (post-2022 rewrite) adds `DynamicType`
and proper nested collections. A from-scratch port could expose:

```hcl
resource "ldap_entry" "admin_group" {
  dn = "cn=admin,ou=groups,${local.ldap_suffix}"
  attributes = {
    objectClass = ["groupOfNames"]
    member      = [ldap_entry.admin_user.dn]
  }
}
```

Same flexibility (open attribute set, multi-valued), much better ergonomics
(typed, validated, autocompletable), no `jsonencode` dance.

The other ecosystem providers (`Ouest-France/ldap`, `dodevops/ldap`) avoid the
`data_json` workaround by going the opposite direction: fixed-schema
`ldap_user` / `ldap_group` / `ldap_ou` resources. That fails for our use case
the moment we touch a custom objectClass — which we already do.

## Problem 2: partial apply on `groupOfNames`

`ldap_entry` is **fully authoritative** on every attribute it touches. For an
entry whose membership is co-managed by Canaille at runtime (admin group, any
role group), this means every `tofu apply` deletes whatever Canaille added and
re-asserts the tofu-declared list.

Mitigation paths considered with the current provider:

- `ignore_attributes = ["member"]` — does not work. Source inspection
  (`client/helpers.go::IgnoreAttributes`) shows the attribute is stripped from
  the payload on **create as well as update**. So the entry is created with no
  `member`, which violates `groupOfNames`'s `MUST member` constraint
  (RFC 4519 §3.5).
- `lifecycle { ignore_changes = [data_json] }` — works but ignores *all* drift,
  including `objectClass`, `cn`, etc. Acceptable as a stopgap; not a real
  solution.
- Move the group entry out of tofu into a one-shot LDIF Job — works, and is
  what we're doing in the short term.

The underlying gap: Terraform has well-established conventions for
non-authoritative resources, and this provider has none of them.

### What `ignore_attributes` actually does today

|                                       | attr **not** in `data_json`                         | attr **in** `data_json`               |
|---------------------------------------|-----------------------------------------------------|---------------------------------------|
| **not in `ignore_attributes`**        | normal: server-added attrs show up as drift         | normal: tofu owns the attribute       |
| **in `ignore_attributes`**            | sensible: filters server-added attrs from state     | **broken — perpetual no-op diff**     |

The broken quadrant is worth spelling out, because it changes how big the
proposed semantic fix actually is:

- Create strips the attribute from the payload. For a `MUST` attribute like
  `member`, LDAP rejects the operation. For optional attrs, it silently isn't
  created.
- Read strips the attribute before writing state, so state's `data_json` never
  contains it.
- The `DiffSuppressFunc` on `data_json` (`resource_entry.go` lines 39–101)
  normalizes case and sorts values, then string-compares. It does **not** apply
  `ignore_attributes` to either side. So HCL has the attribute, state doesn't,
  strings differ, the diff is not suppressed.
- Update strips from both old and new before computing modify ops, so nothing
  is sent on the wire — but the schema-level diff persists.

Net result: a perpetual non-converging plan. Every `plan` shows a change on
`data_json`; every `apply` does nothing on the wire; next `plan` shows the
same change. There is no working configuration in that cell.

So the only useful semantics of the current `ignore_attributes` is the
top-right cell: filter server-side attributes that aren't (and shouldn't be)
in `data_json` — RDN attrs, `entryUUID`, `creatorsName`,
`structuralObjectClass`, etc. For that use case, the proposed
"send on create, ignore on read/update" semantics are bit-identical: there's
nothing to send on create anyway, read still filters, update still ignores.

The "breaking change" framing in the proposal is therefore overstated. It's a
behavior fix that makes the broken quadrant work, with no impact on the only
working use case the field has today.

### What "partial apply" means in the wider Terraform ecosystem

The pattern is variously called **authoritative vs non-authoritative**,
**membership / association resources**, or **junction / edge resources**:

- **GCP IAM:** `google_project_iam_policy` (whole policy) /
  `google_project_iam_binding` (one role's full member list) /
  `google_project_iam_member` (one edge). Three tiers, deliberate.
- **AWS IAM:** inline role policies (authoritative) vs
  `aws_iam_role_policy_attachment` and `aws_iam_user_group_membership`
  (additive).
- **GitHub:** `github_team_members` (full list) vs `github_team_membership`
  (one user).
- **Postgres** (`cyrilgdn/postgresql`): one `postgresql_grant` per grant.
  Junction-row pattern; partial sync emerges for free.

LDAP fits the junction-row pattern naturally: each `(dn, attribute, value)`
tuple is independent.

## Proposed design

Two changes, both enabled by moving to the Plugin Framework.

### 1. Reshape `ldap_entry` and rename the bypass

```hcl
resource "ldap_entry" "admin_group" {
  dn = "cn=admin,ou=groups,${local.ldap_suffix}"
  attributes = {
    objectClass = ["groupOfNames"]
    member      = [ldap_entry.admin_user.dn]   # initial create only
  }
  unmanaged_attributes = ["member"]
}
```

`unmanaged_attributes` (working name; alternatives: `ignore_drift_on`,
`partially_managed_attributes`) replaces the current `ignore_attributes` with
sane semantics:

| phase   | behaviour                                                     |
|---------|---------------------------------------------------------------|
| create  | attribute is sent to the directory as written                 |
| read    | attribute is dropped from the read result before diffing      |
| update  | attribute is omitted from the modify operation                |
| delete  | unaffected (whole entry is removed)                           |

This satisfies `groupOfNames`'s `MUST member` at create time, then leaves the
attribute alone forever after — exactly what the anchor-member pattern needs.

The current `ignore_attributes` can simply have its semantics fixed in place
— per the matrix above, no working configuration exercises the create-strip
behaviour, so changing it is effectively a bug fix. No new field name needed
unless we want to be conservative and ship under a new name with the old one
deprecated for a release.

### 2. Add an additive `ldap_attribute_value` resource

```hcl
resource "ldap_attribute_value" "admin_anchor" {
  dn        = ldap_entry.admin_group.dn
  attribute = "member"
  value     = ldap_entry.admin_user.dn
}
```

One resource = one `(dn, attribute, value)` tuple. CRUD maps directly to LDAP
`MODIFY`:

| TF op  | LDAP op                                  | Edge cases                                                       |
|--------|------------------------------------------|------------------------------------------------------------------|
| Create | `MODIFY ADD attr: value`                 | Result code 20 (`attributeOrValueExists`) → success, adopt.      |
| Read   | search base scope on `dn`, check value   | Value missing → `SetId("")` so TF re-creates.                    |
| Update | n/a                                      | `dn`, `attribute`, `value` are all `ForceNew`.                   |
| Delete | `MODIFY DELETE attr: { value }`          | Codes 16 (`noSuchAttribute`) / 32 (`noSuchObject`) → swallow.    |

Codes 20 and 16 are what make this idempotent at the protocol level — the
LDAP equivalent of `INSERT IGNORE` / `DELETE IF EXISTS`.

## Worked example: admin group

Combining both:

```hcl
resource "ldap_entry" "admin_group" {
  dn = "cn=admin,ou=groups,${local.ldap_suffix}"
  attributes = {
    objectClass = ["groupOfNames"]
    member      = [ldap_entry.admin_user.dn]
  }
  unmanaged_attributes = ["member"]
  depends_on = [ldap_entry.ou_groups, ldap_entry.admin_user]
}

resource "ldap_attribute_value" "admin_anchor" {
  dn        = ldap_entry.admin_group.dn
  attribute = "member"
  value     = ldap_entry.admin_user.dn
}
```

Flow:

1. **First apply.** `ldap_entry.admin_group` creates the group with admin as
   member (RFC `MUST` satisfied). `ldap_attribute_value.admin_anchor` runs
   `MODIFY ADD`, gets code 20, adopts.
2. **Canaille adds another admin.** Group now has two members. Next tofu plan
   sees no diff: `member` is in `unmanaged_attributes` on the entry, and the
   anchor's value is still present.
3. **Someone removes admin from the group via Canaille (mistake).** Next apply
   notices the anchor's value is gone, runs `MODIFY ADD` to restore it. The
   other admin is untouched.
4. **`tofu destroy`.** `ldap_attribute_value` runs `MODIFY DELETE` (or skips on
   code 16 if entry already gone), then `ldap_entry` removes the group.

The `groupOfNames` `MUST member` constraint is satisfied permanently by the
anchor and never has to be re-established. Canaille owns the rest of the
membership without interference.

## Scope

In rough order:

1. Plugin Framework port of `ldap_entry` with `attributes` map. Existing
   `data_json` field can be kept as a deprecated alias for one release.
2. `unmanaged_attributes` semantics fixed (or new field added if breaking
   change is unwanted).
3. New `ldap_attribute_value` resource.
4. Migration notes / state upgrade for existing `ldap_entry` users.

## Sources

- [RFC 4519 §3.5 — `groupOfNames`](https://www.rfc-editor.org/rfc/rfc4519#section-3.5)
- [RFC 4511 §4.6 — Modify operation, result codes 20 and 16](https://www.rfc-editor.org/rfc/rfc4511#section-4.6)
- [Terraform Plugin Framework — Dynamic type](https://developer.hashicorp.com/terraform/plugin/framework/handling-data/types/dynamic)
- [Terraform Plugin SDKv2 schema types](https://developer.hashicorp.com/terraform/plugin/sdkv2/schemas/schema-types)
- [Google IAM three-tier convention (policy / binding / member)](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/google_project_iam)
- [`l-with/terraform-provider-ldap`](https://github.com/l-with/terraform-provider-ldap)
