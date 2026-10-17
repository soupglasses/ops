<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
SPDX-License-Identifier: MIT
-->

# Grafana sign-in

The `OAuth2Client` registers `monitoring.grafana` with the central directory
backend. Its only callback is `https://grafana.finnes.dev/login/generic_oauth`.
Grafana reads the same encrypted client credential from a Secret volume; it never
appears in Helm values or the generated Grafana ConfigMap.

Only members of `monitoring.grafana-admins` can use this sign-in method. Eligible
users receive Grafana server administrator access; missing or unrelated groups
fail strict role mapping. Canaille's group claim contains LDAP group names, and
its `sub` claim supplies the stable directory username. Authorization uses code
exchange with PKCE S256 and client credentials in the token request body. ID-token
signatures are validated against Canaille's HTTPS JWKS endpoint.

Grafana Generic OAuth does not import the OIDC `picture` claim, even though
Canaille exposes the LDAP photo. Native Grafana avatars use Gravatar, which is
disabled here; LDAP profile photos therefore do not appear in Grafana. There is
no native avatar-claim mapping setting. See the pinned
[OAuth user model](https://github.com/grafana/grafana/blob/v13.2.3/pkg/login/social/connectors/generic_oauth.go#L210-L221).

The group initially contains `admin` and `sofie`. Subsequent membership changes
belong to directory administrators: reconciliation owns the group's class and
description, not its members. The namespace opts into the central backend, and
Crossplane's namespace Role permits composing the client Secret and Entry; this
is a trusted identity-control-plane permission.

The normal Grafana login page remains available. Its existing `grafana-auth`
Secret supplies the local recovery administrator independently of OIDC. There is
no automatic redirect to Canaille. Grafana remains stateless: provisioned
settings and directory membership recreate access on a clean deployment.

See [Grafana's Generic OAuth documentation](https://grafana.com/docs/grafana/latest/setup-grafana/configure-access/configure-authentication/generic-oauth/)
for the authentication settings.

The real-server fixture exercises these Helm authentication settings with a
synthetic provider, including group/signature rejection, PKCE, token authentication, and
local recovery login:

```sh
GRAFANA_TEST_IMAGE=grafana/grafana:13.2.3-distroless \
  python3 -m unittest scripts/test_grafana_oidc.py
```

The fixture requires Docker, PyYAML, and cryptography. It creates and removes only a disposable
Grafana container and uses synthetic credentials.
