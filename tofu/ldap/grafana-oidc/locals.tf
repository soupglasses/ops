# SPDX-FileCopyrightText: 2026 Soup Bot <bot+ops@finnes.dev>
#
# SPDX-License-Identifier: 0BSD

locals {
  ldap_suffix    = coalesce(var.ldap_suffix, "dc=${replace(var.domain, ".", ",dc=")}")
  ldap_host      = coalesce(var.ldap_host, "ldap.${var.domain}")
  ldap_bind_user = coalesce(var.ldap_bind_user, "cn=admin,${local.ldap_suffix}")

  admin_user_dn  = "uid=admin,ou=users,${local.ldap_suffix}"
  grafana_url    = "https://grafana.${var.domain}"
  admin_group_dn = "cn=grafana-admins,ou=groups,${local.ldap_suffix}"
}
