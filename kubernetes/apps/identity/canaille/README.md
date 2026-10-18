<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
SPDX-License-Identifier: 0BSD
-->

# Canaille

The deployment uses the [profile-form fix](https://gitlab.com/yaal/canaille/-/merge_requests/351)
from [the Canaille fork](https://gitlab.com/soupglasses/canaille).
[The deployment](app/deployment.yaml) is authoritative for its release tag and image digest.

To rebuild after registry loss, check out the matching release tag in the fork and
build its Dockerfile. That release's `.gitlab-ci.yml` defines the image tests and
publication process. Validate the rebuilt image before publishing it to an available
registry and updating the deployment reference. Keep the source release available
until the deployment returns to an upstream image containing the fix.
