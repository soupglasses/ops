# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
#
# SPDX-License-Identifier: 0BSD

variable "domain" {
  type        = string
  description = "Base domain used for the Canaille issuer and Grafana URL."
  default     = "finnes.dev"
}

variable "oidc_client_secret" {
  type        = string
  description = "Shared secret for the confidential Grafana OIDC client."
  sensitive   = true

  validation {
    condition     = length(var.oidc_client_secret) >= 64
    error_message = "The Grafana OIDC client secret must contain at least 64 characters."
  }
}

variable "ldap_host" {
  type        = string
  description = "LDAP server hostname."
  default     = null
}

variable "ldap_port" {
  type        = number
  description = "LDAP server port."
  default     = 636
}

variable "ldap_bind_user" {
  type        = string
  description = "LDAP bind DN."
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
  description = "Enable TLS for the LDAP connection."
  default     = true
}

variable "ldap_suffix" {
  type        = string
  description = "LDAP base DN."
  default     = null
}
