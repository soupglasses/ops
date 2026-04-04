# SPDX-FileCopyrightText: 2025 Sofie <sofie+git@mailbox.org>
#
# SPDX-License-Identifier: MIT

provider "sops" {}

provider "ldap" {
  host          = local.ldap_host
  port          = var.ldap_port
  bind_user     = local.ldap_bind_user
  bind_password = local.ldap_bind_password
  tls           = var.ldap_tls
}
