# SPDX-FileCopyrightText: 2026 OpenAI
#
# SPDX-License-Identifier: MIT

# The directory is restored by VolSync before this module runs. Reconstruct lost
# controller state from those entries instead of trying to recreate them.

import {
  to = ldap_entry.admin_group
  id = "cn=admin,ou=groups,${local.ldap_suffix}"
}

import {
  to = ldap_entry.ou_users
  id = "ou=users,${local.ldap_suffix}"
}

import {
  to = ldap_entry.ou_system
  id = "ou=system,${local.ldap_suffix}"
}

import {
  to = ldap_entry.ou_groups
  id = "ou=groups,${local.ldap_suffix}"
}

import {
  to = ldap_entry.ou_policies
  id = "ou=policies,${local.ldap_suffix}"
}

import {
  to = ldap_entry.ou_oauth
  id = "ou=oauth,${local.ldap_suffix}"
}

import {
  to = ldap_entry.ou_oauth_clients
  id = "ou=clients,ou=oauth,${local.ldap_suffix}"
}

import {
  to = ldap_entry.ou_oauth_authorizations
  id = "ou=authorizations,ou=oauth,${local.ldap_suffix}"
}

import {
  to = ldap_entry.ou_oauth_consents
  id = "ou=consents,ou=oauth,${local.ldap_suffix}"
}

import {
  to = ldap_entry.ou_oauth_tokens
  id = "ou=tokens,ou=oauth,${local.ldap_suffix}"
}

import {
  to = ldap_entry.password_policy
  id = "cn=password,ou=policies,${local.ldap_suffix}"
}

import {
  to = ldap_entry.system_password_policy
  id = "cn=system,ou=policies,${local.ldap_suffix}"
}

import {
  to = ldap_entry.auth_user
  id = "uid=auth,ou=system,${local.ldap_suffix}"
}

import {
  to = ldap_entry.admin_user
  id = "uid=admin,ou=users,${local.ldap_suffix}"
}
