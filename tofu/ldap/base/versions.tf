# SPDX-FileCopyrightText: 2025 Sofie <sofie+git@mailbox.org>
#
# SPDX-License-Identifier: MIT

terraform {
  required_version = ">= 1.6.0"
  required_providers {
    ldap = {
      source  = "l-with/ldap"
      version = "~> 0.12"
    }
  }
}
