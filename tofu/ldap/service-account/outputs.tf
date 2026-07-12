# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
#
# SPDX-License-Identifier: 0BSD

# Output names become the keys of the writeOutputsToSecret Secret; kept
# env-style so apps can consume it via envFrom directly.

output "LDAP_BIND_DN" {
  value = ldap_entry.service_account.dn
}

output "LDAP_BIND_PASSWORD" {
  value     = random_password.bind.result
  sensitive = true
}

output "LDAP_URI" {
  value = "ldaps://${local.ldap_host}:${var.ldap_port}"
}

output "LDAP_BASE_DN" {
  value = local.ldap_suffix
}
