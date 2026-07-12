# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
#
# SPDX-License-Identifier: 0BSD

locals {
  ldap_suffix    = coalesce(var.ldap_suffix, "dc=${replace(var.domain, ".", ",dc=")}")
  ldap_host      = coalesce(var.ldap_host, "ldap.${var.domain}")
  ldap_bind_user = coalesce(var.ldap_bind_user, "cn=admin,${local.ldap_suffix}")
}
