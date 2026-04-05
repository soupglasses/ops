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
