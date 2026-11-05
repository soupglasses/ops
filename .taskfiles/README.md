<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
SPDX-License-Identifier: 0BSD
-->

# Application acceptance checks

`task app:test NAMESPACE=... RELEASE=...` runs the Helm release's native test hooks.
Checks must preserve application configuration and data; destructive integration
tests belong in disposable fixtures.

`task app:verify-render` checks Jellyfin's storage, no-backup and private-routing
contracts in the final Flux render. Both tasks are defined in
[application.yaml](application.yaml).
