# SPDX-FileCopyrightText: 2025 Sofie <sofie+git@mailbox.org>
#
# SPDX-License-Identifier: MIT

# Initial admin user -- bootstrap account for Canaille administration.
resource "ldap_entry" "admin_user" {
  dn = "uid=admin,ou=users,${local.ldap_suffix}"
  data_json = jsonencode({
    objectClass  = ["inetOrgPerson", "organizationalPerson", "person"]
    cn           = ["Admin"]
    sn           = ["Admin"]
    mail         = ["admin@${var.domain}"]
    userPassword = [local.admin_password]
  })
  depends_on = [ldap_entry.ou_users]
}
