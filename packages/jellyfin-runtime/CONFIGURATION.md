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
including LDAP user links, photo hashes, and unrelated settings. Arrays are owned as complete values. Media libraries are additive:
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
HTTP Request managed resource. LDAP entries remain Crossplane resources.

## Plugin compatibility and photos

`plugins.lock.json` pins Jellyfin 12.2 and the unmodified upstream LDAP 24
release ZIP by SHA-256, with its source revision recorded for license provenance
and rebuilding. There is no local plugin patch or .NET build stage. Upstream LDAP code is MIT; Jellyfin retains its GPL license. Newly
authored integration code is MIT, and builds preserve upstream license notices.

Users sign in with their directory username and password. The upstream LDAP
plugin creates their Jellyfin account and records its LDAP UID. Its scheduled
profile-image task then reads `jpegPhoto` for those linked users. The application
group filter controls who can sign in. No Jellyfin SSO plugin or OAuth client is
required.

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

The integration test requires Docker and deletes its own synthetic directory and
application containers. It signs in through the ordinary LDAP password endpoint,
runs upstream scheduled photo synchronization, checks the returned JPEG and LDAP
photo hash, reconciles settings drift without losing the LDAP link, and signs in
again with the same Jellyfin user ID.
