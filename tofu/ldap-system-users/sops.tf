# SPDX-FileCopyrightText: 2025 Sofie <sofie+git@mailbox.org>
#
# SPDX-License-Identifier: MIT

data "sops_file" "cluster_secrets" {
  count       = var.use_sops ? 1 : 0
  source_file = "../../kubernetes/components/common/config/cluster-secrets.sops.yaml"
}

data "sops_file" "ldap_secrets" {
  count       = var.use_sops ? 1 : 0
  source_file = "../../kubernetes/apps/identity/ldap-system-users/app/secret.sops.yaml"
}

locals {
  ldap_suffix        = coalesce(var.ldap_suffix, "dc=${replace(var.domain, ".", ",dc=")}")
  ldap_host          = coalesce(var.ldap_host, "ldap.${var.domain}")
  ldap_bind_user     = coalesce(var.ldap_bind_user, "cn=admin,${local.ldap_suffix}")
  ldap_bind_password = var.use_sops ? data.sops_file.ldap_secrets[0].data["ldap_bind_password"] : var.ldap_bind_password
  auth_user_password = var.use_sops ? data.sops_file.ldap_secrets[0].data["auth_user_password"] : var.auth_user_password
  admin_password     = var.use_sops ? data.sops_file.ldap_secrets[0].data["admin_password"] : var.admin_password
}
