# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
#
# SPDX-License-Identifier: 0BSD

# One bind account per app under ou=system. This module owns exactly this one
# entry so its state can never fight the base module or another app's.
#
# The OU and the cn=system policy come from tofu/ldap/base; ordering is
# enforced at the Flux layer (the app's Kustomization dependsOn
# ldap-system-users), not by a cross-state reference.

resource "random_password" "bind" {
  length  = 48
  special = false
}

resource "ldap_entry" "service_account" {
  dn = "uid=${var.app},ou=system,${local.ldap_suffix}"
  data_json = jsonencode({
    objectClass = ["inetOrgPerson", "organizationalPerson", "person"]
    cn          = ["${var.app} service account"]
    sn          = [var.app]
    # Stored as given (no server-side hashing on add); generated, machine-only,
    # and carried over LDAPS. See docs/ldap-service-accounts.md.
    userPassword      = [random_password.bind.result]
    pwdPolicySubentry = ["cn=system,ou=policies,${local.ldap_suffix}"]
  })
  restrict_attributes = ["objectClass", "cn", "sn", "userPassword", "pwdPolicySubentry"]
}
