<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
SPDX-License-Identifier: MIT
-->

# Identity composition tests

These fixtures verify the ops-owned OAuth2Client, GroupOfNames and ServiceAccount compositions
against real OpenLDAP and Canaille. The generic LDAP controller is installed as a
native Crossplane Provider package; its source and release pipeline live in
[soupglasses/provider-ldap](https://github.com/soupglasses/provider-ldap).

Build the fixture image and run the disposable kind test from the repository root:

```sh
docker build --target tests -t ops-identity-tests:development packages/identity-tests
packages/identity-tests/kind-test.sh
```

The test uses the provider package pinned in the production manifest. To test an
unpublished build, set `PROVIDER_PACKAGE=/absolute/path/provider-ldap.xpkg`. Local
packages are pushed to an HTTP registry trusted only by the disposable kind node.
`TESTS_IMAGE` overrides the fixture image; `INTEGRATION_TEST_PATTERN` selects Go
tests. No production credentials or LDAP server are used.

The suite checks ops compositions through LDAP and Canaille: client credentials,
group membership and owner preservation, disabled placeholder authentication,
renames, and generated service credentials. Generic provider behavior is tested
in the standalone provider repository.
Fixtures and test permissions belong here; application declarations remain in
[`kubernetes/apps/identity/ldap-provider/app`](../../kubernetes/apps/identity/ldap-provider/app).
