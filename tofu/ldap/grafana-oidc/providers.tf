# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
#
# SPDX-License-Identifier: 0BSD

provider "ldap" {
  host          = local.ldap_host
  port          = var.ldap_port
  bind_user     = local.ldap_bind_user
  bind_password = var.ldap_bind_password
  tls           = var.ldap_tls
}
