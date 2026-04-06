# SPDX-FileCopyrightText: 2025 Sofie <sofie+git@mailbox.org>
#
# SPDX-License-Identifier: MIT

resource "ldap_entry" "ou_users" {
  dn = "ou=users,${local.ldap_suffix}"
  data_json = jsonencode({
    objectClass = ["organizationalUnit"]
  })
}

resource "ldap_entry" "ou_system" {
  dn = "ou=system,${local.ldap_suffix}"
  data_json = jsonencode({
    objectClass = ["organizationalUnit"]
  })
}

resource "ldap_entry" "ou_groups" {
  dn = "ou=groups,${local.ldap_suffix}"
  data_json = jsonencode({
    objectClass = ["organizationalUnit"]
  })
}

resource "ldap_entry" "ou_policies" {
  dn = "ou=policies,${local.ldap_suffix}"
  data_json = jsonencode({
    objectClass = ["organizationalUnit"]
  })
}

resource "ldap_entry" "ou_oauth" {
  dn = "ou=oauth,${local.ldap_suffix}"
  data_json = jsonencode({
    objectClass = ["organizationalUnit"]
  })
}

resource "ldap_entry" "ou_oauth_clients" {
  dn = "ou=clients,ou=oauth,${local.ldap_suffix}"
  data_json = jsonencode({
    objectClass = ["organizationalUnit"]
  })
  depends_on = [ldap_entry.ou_oauth]
}

resource "ldap_entry" "ou_oauth_authorizations" {
  dn = "ou=authorizations,ou=oauth,${local.ldap_suffix}"
  data_json = jsonencode({
    objectClass = ["organizationalUnit"]
  })
  depends_on = [ldap_entry.ou_oauth]
}

resource "ldap_entry" "ou_oauth_consents" {
  dn = "ou=consents,ou=oauth,${local.ldap_suffix}"
  data_json = jsonencode({
    objectClass = ["organizationalUnit"]
  })
  depends_on = [ldap_entry.ou_oauth]
}

resource "ldap_entry" "ou_oauth_tokens" {
  dn = "ou=tokens,ou=oauth,${local.ldap_suffix}"
  data_json = jsonencode({
    objectClass = ["organizationalUnit"]
  })
  depends_on = [ldap_entry.ou_oauth]
}
