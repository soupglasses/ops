# SPDX-FileCopyrightText: 2025 Sofie <sofie+git@mailbox.org>
#
# SPDX-License-Identifier: MIT

resource "ldap_entry" "password_policy" {
  dn = "cn=password,ou=policies,${local.ldap_suffix}"
  data_json = jsonencode({
    objectClass        = ["pwdPolicy", "person", "top"]
    cn                 = ["password"]
    sn                 = ["password"]
    pwdAttribute       = ["userPassword"]
    pwdMustChange      = ["TRUE"]
    pwdLockout         = ["TRUE"]
    pwdAllowUserChange = ["TRUE"]
    pwdGraceAuthNLimit = ["1"]
    pwdMaxFailure      = ["999"]
  })
  depends_on = [ldap_entry.ou_policies]
}
