# SPDX-FileCopyrightText: 2026 Soup Bot <bot+ops@finnes.dev>
#
# SPDX-License-Identifier: 0BSD

output "oidc_client_id" {
  value       = "grafana"
  description = "Grafana's Canaille OIDC client ID."
}

output "oidc_issuer_url" {
  value       = "https://auth.${var.domain}"
  description = "Canaille's OIDC issuer URL."
}

output "grafana_admin_group" {
  value       = "grafana-admins"
  description = "Canaille group allowed to sign in to Grafana."
}
