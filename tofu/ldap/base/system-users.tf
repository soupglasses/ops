# SPDX-FileCopyrightText: 2025 Sofie <sofie+git@mailbox.org>
#
# SPDX-License-Identifier: MIT

# Canaille bind account -- used for LDAP authentication lookups.
# TODO: Rename
resource "ldap_entry" "auth_user" {
  dn = "uid=auth,ou=system,${local.ldap_suffix}"
  data_json = jsonencode({
    objectClass        = ["inetOrgPerson", "organizationalPerson", "person"]
    cn                 = ["System Auth User"]
    sn                 = ["Auth"]
    userPassword       = [var.admin_user_password_hash]
    pwdPolicySubentry  = ["cn=system,ou=policies,${local.ldap_suffix}"]
  })
  restrict_attributes = ["objectClass", "cn", "sn", "userPassword", "pwdPolicySubentry"]
  depends_on = [ldap_entry.ou_system, ldap_entry.system_password_policy]
}
