<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
SPDX-License-Identifier: 0BSD
-->

# Application acceptance checks

`task app:test NAMESPACE=... RELEASE=...` runs the Helm release's native test hooks.
For Deployments, use `task app:test NAMESPACE=... APP=...`. The Deployment metadata
must declare `testing.ops.finnes.dev/container` and `testing.ops.finnes.dev/command`.
The command is a JSON array of arguments. The runner waits for rollout and executes
it without a shell using the operator's Kubernetes permissions. Checks must preserve
application configuration and data; destructive integration tests belong in a fixture.

`task app:verify-render` checks Jellyfin's storage, no-backup and private-routing
contracts in the final Flux render. These tasks are defined in
[application.yaml](application.yaml); the acceptance runner is
[application-test.py](../scripts/application-test.py).
