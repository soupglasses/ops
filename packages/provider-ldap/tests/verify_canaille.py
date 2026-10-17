# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT

"""Check that Canaille recognizes an LDAP-provisioned confidential client."""

import base64
import os

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
