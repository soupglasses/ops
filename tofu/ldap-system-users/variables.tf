# SPDX-FileCopyrightText: 2025 Sofie <sofie+git@mailbox.org>
#
# SPDX-License-Identifier: MIT

variable "use_sops" {
  type        = bool
  description = "Use SOPS to decrypt secrets. Set to false when variables are provided externally (e.g., via tofu-controller)."
  default     = true
}

variable "domain" {
  type        = string
  description = "Base domain"
  default     = "finnes.dev"
}

variable "ldap_host" {
  type        = string
  description = "LDAP server hostname"
  default     = null
}

variable "ldap_port" {
  type        = number
  description = "LDAP server port"
  default     = 636
}

variable "ldap_bind_user" {
  type        = string
  description = "LDAP bind DN"
  default     = null
}

variable "ldap_bind_password" {
  type        = string
  description = "LDAP bind password. Only required when use_sops is false."
  sensitive   = true
  default     = null
}

variable "ldap_tls" {
  type        = bool
  description = "Enable TLS for LDAP connection"
  default     = true
}

variable "ldap_suffix" {
  type        = string
  description = "LDAP base DN"
  default     = null
}

variable "auth_user_password" {
  type        = string
  description = "Password for the auth system user. Only required when use_sops is false."
  sensitive   = true
  default     = null
}

variable "admin_password" {
  type        = string
  description = "Password for the initial admin user. Only required when use_sops is false."
  sensitive   = true
  default     = null
}
