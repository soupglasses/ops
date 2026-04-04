# SPDX-FileCopyrightText: 2025 Sofie <sofie+git@mailbox.org>
#
# SPDX-License-Identifier: MIT

# Canaille bind account -- used for LDAP authentication lookups.
# Restricted by ACLs to auth-only access.
resource "ldap_entry" "auth_user" {
  dn = "uid=auth,ou=system,${local.ldap_suffix}"
  data_json = jsonencode({
    objectClass  = ["inetOrgPerson", "organizationalPerson", "person"]
    uid          = ["auth"]
    cn           = ["System Auth User"]
    sn           = ["Auth"]
    userPassword = [local.auth_user_password]
  })
  depends_on = [ldap_entry.ou_system]
}
