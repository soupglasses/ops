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

The pinned `soupglasses/grafana:13.2.4-oidc.1` build adds Generic OAuth support
for Canaille's HTTPS `picture` claim. Profile and navigation pictures synchronize
at login; removed or invalid pictures use the default avatar. Gravatar remains
disabled. Pictures load directly in the browser, so Canaille's signed photo URL
must remain accessible to the user.

The build is Grafana 13.2.4 plus
[one feature commit](https://github.com/soupglasses/grafana/commit/89c2ebbeac98fcc251b0766861cd77af425fdfbe).
The [signed release](https://github.com/soupglasses/grafana/releases/tag/v13.2.4-oidc.1)
contains the source archive, patch, Dockerfile, binary and checksums. The pinned
GHCR image is anonymously pullable. The fork's `build/oidc-picture-release` branch
publishes it from these verified artifacts. The release recipe uses
Go 1.26.9 and the stock 13.2.4 image pinned by digest. If that base image becomes
unavailable, build the complete image with the same source tree's Dockerfile.
The patch adds a nullable field to Grafana's disposable authentication database;
no directory data or credentials change. To roll back, remove the image override
and let Flux restore the chart's stock image; users sign in again.

Manage group membership and ownership in Canaille. Git declares the group and
its description without granting human access. The platform sentinel satisfies
LDAP's required member attribute for new groups. The namespace opts into the
central backend. Crossplane supplies Secret permissions; the LDAP chart supplies
the Entry permission once for all identity compositions.

The normal Grafana login page remains available. Its existing `grafana-auth`
Secret supplies the local recovery administrator independently of OIDC. There is
no automatic redirect to Canaille. Grafana remains stateless: provisioned
settings and directory membership recreate access on a clean deployment.

See [Grafana's Generic OAuth documentation](https://grafana.com/docs/grafana/latest/setup-grafana/configure-access/configure-authentication/generic-oauth/)
for the authentication settings.

The real-server fixture exercises these Helm authentication settings with a
synthetic provider, including profile/navigation picture updates and removal,
group/signature rejection, PKCE, token authentication, and local recovery login:

```sh
GRAFANA_TEST_IMAGE=ghcr.io/soupglasses/grafana:13.2.4-oidc.1 \
  python3 -m unittest scripts/test_grafana_oidc.py
```

The fixture requires Docker, PyYAML, and cryptography. It creates and removes only a disposable
Grafana container and uses synthetic credentials.
