# SPDX-FileCopyrightText: 2025 Sofie <sofie+git@mailbox.org>
#
# SPDX-License-Identifier: MIT

# Canaille bind account -- used for LDAP authentication lookups.
resource "ldap_entry" "auth_user" {
  dn = "uid=auth,ou=system,${local.ldap_suffix}"
  data_json = jsonencode({
    objectClass        = ["inetOrgPerson", "organizationalPerson", "person"]
    cn                 = ["System Auth User"]
    sn                 = ["Auth"]
    userPassword       = [local.auth_user_password]
    pwdPolicySubentry  = ["cn=system,ou=policies,${local.ldap_suffix}"]
  })
  depends_on = [ldap_entry.ou_system, ldap_entry.system_password_policy]
}
