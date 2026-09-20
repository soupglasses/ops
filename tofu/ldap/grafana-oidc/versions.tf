# SPDX-FileCopyrightText: 2026 Soup Bot <bot+ops@finnes.dev>
#
# SPDX-License-Identifier: 0BSD

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    ldap = {
      source  = "l-with/ldap"
      version = "~> 0.12"
    }
  }
}
