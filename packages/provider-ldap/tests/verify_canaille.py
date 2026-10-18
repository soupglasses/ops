# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT

"""Check Canaille compatibility with provider-provisioned LDAP identities."""

import base64
import os
import sys

from canaille import create_app
from canaille.oidc.provider import query_client

app = create_app(
    config={
        "TESTING": True,
        "SECRET_KEY": "disposable-fixture-signing-key",
        "SERVER_NAME": "auth.example.test",
        "CANAILLE": {"DATABASE": "ldap"},
        "CANAILLE_OIDC": {},
        "CANAILLE_LDAP": {
            "URI": os.environ["LDAP_TEST_URI"],
            "ROOT_DN": "dc=example,dc=test",
            "BIND_DN": "cn=admin,dc=example,dc=test",
            "BIND_PW": "disposable-local-fixture-only",
            "USER_BASE": "ou=users,dc=example,dc=test",
            "GROUP_BASE": "ou=groups,dc=example,dc=test",
        },
    },
    init_backend=False,
)

if group_name := os.environ.get("LDAP_TEST_GROUP"):
    from canaille.app import models

    with app.test_request_context(), app.backend.session():
        group = app.backend.get(models.Group, display_name=group_name)
        sentinel = app.backend.get(models.User, user_name="group-sentinel")
        human = app.backend.get(models.User, user_name="alice")
        assert group is not None and sentinel is not None and human is not None
        group.members = [sentinel, human]
        group.owner = human
        app.backend.save(group)
        app.backend.reload(group)
        assert set(group.members) == {sentinel, human}
        assert group.owner == human
        group.members = [sentinel]
        group.owner = None
        app.backend.save(group)
        app.backend.reload(group)
        assert group.members == [sentinel]
        assert group.owner is None
    print("Canaille adds and removes the last human member while retaining the sentinel")
    sys.exit(0)

secret = os.environ.get("LDAP_TEST_CLIENT_SECRET", "fixture-client-secret-2")
with app.test_request_context():
    with app.backend.session():
        client = query_client("immich")
        assert client is not None, "Canaille did not find the provider-created client"
        assert client.client_id == "immich"
        assert client.check_client_secret(secret)
        assert client.check_redirect_uri("https://immich.example.test/auth/login")
        assert not client.check_redirect_uri("https://wrong.example.test/callback")
        assert set(client.scope) == {"openid", "profile", "email"}
        assert client.audience[0].client_id == "immich"

credentials = base64.b64encode(f"immich:{secret}".encode()).decode()
response = app.test_client().post(
    "/oauth/token",
    base_url="https://auth.example.test",
    headers={"Authorization": f"Basic {credentials}"},
    data={
        "grant_type": "authorization_code",
        "code": "nonexistent-fixture-code",
        "redirect_uri": "https://immich.example.test/auth/login",
    },
)
assert response.status_code == 400 and response.json["error"] == "invalid_grant", (
    "Canaille did not accept client authentication before checking the grant",
    response.status_code,
    response.json.get("error"),
)
print("Canaille 0.3.6 recognizes the LDAP client, redirect, scopes, audience, and rotated credentials")
