# SPDX-FileCopyrightText: 2025 Sofie <sofie+git@mailbox.org>
#
# SPDX-License-Identifier: MIT

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
  description = "LDAP bind password."
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

variable "admin_user_password_hash" {
  type        = string
  description = "RFC2307 password hash for the auth system user."
  sensitive   = true
  default     = null
}

variable "admin_password_hash" {
  type        = string
  description = "RFC2307 password hash for the initial admin user."
  sensitive   = true
  default     = null
}
