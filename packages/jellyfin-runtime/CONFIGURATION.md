<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
SPDX-License-Identifier: MIT
-->

# Jellyfin configuration

The configuration sidecar reconciles an explicit subset of Jellyfin's API
configuration. It reads `/desired/configuration.json` and individual credential
files in `/credentials`. Responses and session tokens remain in memory; they are
not written to Kubernetes resource status, files, logs, or events. The sidecar
needs no Kubernetes API permissions.

A resource declares an API `path`, nested `values`, and optional `secretValues`:

```json
{
  "path": "/Plugins/958aad66-3784-4d2a-b89a-a7b6fab6e25c/Configuration",
  "values": {
    "LdapServer": "ldap.finnes.dev",
    "EnableLdapProfileImageSync": true
  },
  "secretValues": [
    {"path": ["LdapBindPassword"], "file": "LDAP_BIND_PASSWORD"}
  ]
}
```

Each poll reads the latest desired configuration and credential files, GETs the
current API configuration, compares the declared fields, and POSTs a merged
configuration when those fields differ. Undeclared object fields survive,
including LDAP user links and photo hashes, SSO canonical links, and unrelated
providers. Arrays are owned as complete values. Media libraries are additive:
missing libraries and declared paths are created without deleting other libraries
or paths; an existing library with a conflicting collection type fails visibly.
Plugin repositories are disabled because plugin binaries are maintained through
the pinned runtime image. Configuration is reconciled over
pod loopback only, with redirects disabled. Jellyfin's API has no conditional
configuration updates, so a concurrent plugin write between GET and POST remains
a race; reconciliation cannot provide transactional field ownership.

On an incomplete startup wizard, the sidecar creates the local `ops-recovery`
administrator with `JELLYFIN_ADMIN_PASSWORD`, applies startup settings, and
completes setup. On existing installations it authenticates that administrator
without resetting credentials. Keep this recovery credential in encrypted
GitOps configuration, together with application backups. Sessions stay in memory
and are reused until rejected; a restart reauthenticates instead of depending on
a Kubernetes API-key Secret.

Changing the saved recovery password alone does not rotate an existing account;
rotate it through the application before updating the retained recovery value.

`configuration.py --check` authenticates and compares the managed fields without
changing settings. It exits unsuccessfully on incomplete setup or drift. Normal
reconciliation retries after errors; errors contain HTTP status codes without
response bodies. A timestamp-only readiness marker becomes unhealthy on failure
or after three minutes without a successful reconciliation.

## Upstream provider limitation

`crossplane-contrib/provider-http` v1.0.15
([f0d13b6](https://github.com/crossplane-contrib/provider-http/tree/f0d13b67675737a0a883b4c17df8906b2648f3ba))
cannot safely perform this credential-bearing GET/merge/POST workflow:

- `requestgen.GenerateRequestDetails` resolves observed secret placeholders before
  evaluating the JQ merge. Returned credentials then enter the supposedly masked
  `Body.Encrypted` field, which the HTTP client logs and stores in request status.
- `ApplyResponseDataToSecrets` returns normally when writing its masking Secret
  fails. The response retains plaintext credentials and observation continues.

This is why application configuration uses a small sidecar rather than a generic
HTTP Request managed resource. LDAP entries and OAuth2 client registration remain
Crossplane resources.

## Plugin compatibility and photos

`plugins.lock.json` pins Jellyfin 10.11.11, LDAP 23, and SSO 4.0.0.4. LDAP 24 targets
Jellyfin 12, while the pinned SSO plugin targets 10.11. Upstream LDAP code is MIT;
Jellyfin and SSO retain their upstream GPL licenses. Newly authored integration
code and the LDAP patch are MIT. Build steps preserve upstream license notices;
no generated source or upstream license headers are rewritten.

The LDAP patch lets the scheduled photo-sync task discover SSO-created users
already assigned to the LDAP authentication provider. It requires a unique LDAP
result, an exact configured username match, and an unclaimed LDAP UID. It does not
require the user's password, change authentication providers, or reset existing
photo hashes. Configure matching LDAP and OIDC usernames (`uid` and
`preferred_username`), and set SSO `DefaultProvider` to
`Jellyfin.Plugin.LDAP_Auth.LdapAuthenticationProviderPlugin`.

Canaille's `groups` scope emits group display names (LDAP `cn`), so SSO roles use
`media.jellyfin-users`; the LDAP filter uses that group's complete DN. Register
this client with `client_secret_post`: the pinned [OIDC client library defaults
to POST-body client credentials](https://github.com/DuendeSoftware/foss/blob/imoc-6.0.1/identity-model-oidc-client/src/IdentityModel.OidcClient/OidcClientOptions.cs#L210).

## Verification

Run the HTTP-fixture tests:

```sh
python3 -m unittest discover -s packages/jellyfin-runtime -p test_configuration.py -v
```

Build and test real Jellyfin with a synthetic OpenLDAP directory:

```sh
packages/jellyfin-runtime/build.sh ops-jellyfin:development
JELLYFIN_INTEGRATION_IMAGE=ops-jellyfin:development \
  python3 -m unittest discover -s packages/jellyfin-runtime/tests \
  -p test_identity_integration.py -v
```

The integration test requires Docker and local OpenLDAP tools/schemas. It creates
and deletes its own container and LDAP process. Its SSO-first test reproduces the
account state produced by SSO (new user plus LDAP provider assignment); it does
not replace a complete browser-based OIDC authorization-flow test.
