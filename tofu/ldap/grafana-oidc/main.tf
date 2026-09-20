# SPDX-FileCopyrightText: 2026 Soup Bot <bot+ops@finnes.dev>
#
# SPDX-License-Identifier: 0BSD

# Keep Grafana access separate from Canaille-wide administration. Membership is
# bootstrapped with the recovery admin, then owned by Canaille rather than Tofu.
resource "ldap_entry" "grafana_admins" {
  dn = local.admin_group_dn

  data_json = jsonencode({
    objectClass = ["groupOfNames"]
    member      = [local.admin_user_dn]
    description = ["Members can sign in to Grafana as Grafana administrators."]
  })

  restrict_attributes = ["objectClass", "description"]
}

# Grafana is a server-side client, so use a confidential authorization-code
# client. PKCE is also enabled in Grafana as defense in depth.
resource "ldap_entry" "grafana_oidc" {
  dn = "oauthClientID=grafana,ou=clients,ou=oauth,${local.ldap_suffix}"

  data_json = jsonencode({
    objectClass                  = ["oauthClient"]
    oauthClientID                = ["grafana"]
    oauthClientName              = ["Grafana"]
    oauthClientSecret            = [var.oidc_client_secret]
    oauthRedirectURIs            = ["${local.grafana_url}/login/generic_oauth"]
    oauthPostLogoutRedirectURI   = ["${local.grafana_url}/login"]
    oauthGrantType               = ["authorization_code"]
    oauthResponseType            = ["code"]
    oauthScope                   = ["openid", "profile", "email", "groups"]
    oauthTokenEndpointAuthMethod = ["client_secret_basic"]
  })

  # Canaille owns consent, audiences, and runtime timestamps. In particular,
  # omitting the optional issue date avoids a timestamp-driven perpetual diff.
  # Tofu owns only registration details whose drift could weaken or break auth.
  restrict_attributes = [
    "objectClass",
    "oauthClientID",
    "oauthClientName",
    "oauthClientSecret",
    "oauthRedirectURIs",
    "oauthPostLogoutRedirectURI",
    "oauthGrantType",
    "oauthResponseType",
    "oauthScope",
    "oauthTokenEndpointAuthMethod",
  ]
}
