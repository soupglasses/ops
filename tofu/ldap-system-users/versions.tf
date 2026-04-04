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
    sops = {
      source  = "carlpett/sops"
      version = "~> 1.0"
    }
  }
  backend "s3" {
    bucket = "soupglasses"
    key    = "tofu/ldap-system-users/terraform.tfstate"
    region = "auto"
    endpoints {
      s3 = "https://hel1.your-objectstorage.com"
    }
    skip_credentials_validation = true
    skip_metadata_api_check     = true
    skip_requesting_account_id  = true
    use_path_style              = true
  }
}
