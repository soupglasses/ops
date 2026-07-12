# SPDX-FileCopyrightText: 2025 Sofie <sofie+git@mailbox.org>
#
# SPDX-License-Identifier: MIT

locals {
  ldap_suffix = coalesce(var.ldap_suffix, "dc=${replace(var.domain, ".", ",dc=")}")
  ldap_host   = coalesce(var.ldap_host, "ldap.${var.domain}")
  ldap_bind_user = coalesce(var.ldap_bind_user, "cn=admin,${local.ldap_suffix}")
}
