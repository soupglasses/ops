<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
SPDX-License-Identifier: MIT
-->

# Directory identity APIs

The native [LDAP provider](https://github.com/soupglasses/provider-ldap) reconciles
LDAP entries. This directory adds our directory defaults and Canaille OAuth schema
through Crossplane compositions. Flux orders installation; Crossplane reconciles
application declarations.

Keep declarations beside their application:

```yaml
apiVersion: ops.finnes.dev/v1alpha1
kind: GroupOfNames
metadata:
  name: jellyfin-users
  namespace: media
spec:
  cn: media.jellyfin.user
  description: Users permitted to sign in to Jellyfin
---
apiVersion: ops.finnes.dev/v1alpha1
kind: ServiceAccount
metadata:
  name: jellyfin-bind
  namespace: media
spec:
  cn: media.jellyfin
```

The shared backend defaults to `directory`. Namespace labels and allowed LDAP
bases restrict its use; shared entries use namespace-prefixed names. The runtime
has read-only namespace access to evaluate these labels, which Crossplane's default
provider permissions do not include. Groups default to `ou=groups`, service accounts
to `ou=system`, under our directory suffix.
`backendRef` and `baseDN` allow explicit alternatives. Group and service-account
name or base changes rename the same LDAP entry through its stable UUID.

Canaille owns human memberships and owners. Groups declare only a disabled
placeholder member through `sharedAttributes`, preserving all other members and
object classes. The placeholder lives under `ou=users` so Canaille can preserve
its membership when saving groups. Its expired password policy, empty password,
and bind-denying ACL disable authentication; its display name identifies it as a
system placeholder. Changing `sentinelDN` retires the previously claimed placeholder
in the same atomic LDAP modification that adds its replacement.

Service accounts generate a persistent 32-character password and a bcrypt LDAP
hash in a composed Secret. `status.secretRef` publishes its name and confirmed
resource version after LDAP observes that exact hash. Applications consume the
`password` key; LDAP consumes `ldap-password`. The system password policy applies
by default; `passwordPolicyDN` selects an alternative. Change `rotationID` to rotate.
Jellyfin references the service account through its settings API, so no generated
Secret name needs to appear in application configuration.

`OAuth2Client` remains a separate Canaille-specific API. It accepts `clientID`,
`redirectURIs`, and a local `clientSecretRef` for confidential clients; public
clients select `tokenEndpointAuthMethod: none`.

All three APIs accept `deletionPolicy: Delete` (default) or `Orphan`, translated
into native Entry management policies. Orphan retains the LDAP object; composed
Kubernetes Secrets follow their owning composite's lifecycle.

[Identity tests](../../../../packages/identity-tests/README.md) exercise the
compositions against disposable OpenLDAP and Canaille fixtures.
