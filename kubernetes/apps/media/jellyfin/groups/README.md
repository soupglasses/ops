<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
SPDX-License-Identifier: MIT
-->

# Jellyfin groups

These declarations belong to Jellyfin's application configuration. The reusable
`GroupOfNames` composition contains no Jellyfin names, membership or role logic.

The resources reconcile in `identity` because they use the existing
`directory-admin` ProviderConfig to maintain legacy unprefixed group names. They
remain stored alongside the application; namespace location does not determine
source ownership. Tenant-facing media resources still use the guarded cluster
provider and namespace-prefixed DNs.

`jellyfin.user` permits login and `jellyfin.admin` grants administrator privileges.
Membership and ownership seeds apply only if the LDAP group does not exist.
Manage subsequent changes in Canaille. Removing a CR retains the LDAP group;
recreating it adopts the same group and preserves human changes.
