# SPDX-FileCopyrightText: 2025 Sofie <sofie+git@mailbox.org>
#
# SPDX-License-Identifier: MIT

resource "ldap_entry" "admin_group" {
  dn = "cn=admin,ou=groups,${local.ldap_suffix}"
  data_json = jsonencode({
    objectClass = ["groupOfNames"]
    cn          = ["admin"]
    member = [
      "cn=admin,${local.ldap_suffix}",
      ldap_entry.admin_user.dn,
    ]
  })
  depends_on = [ldap_entry.ou_groups, ldap_entry.admin_user]
}
