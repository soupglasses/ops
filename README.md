<!--
SPDX-FileCopyrightText: 2025 Sofie <sofie+git@mailbox.org>

SPDX-License-Identifier: MIT
-->

# 👩‍💻 Infra 👩‍💻

Just another home operations Talos Linux cluster.

## Overview

Kubernetes cluster is deployed on top of Talos Linux, bootstrapped with [Talos Production Notes](https://www.talos.dev/v1.10/introduction/prodnotes/).

Applications are deployed using FluxCD.

### Network layout

Mikrotik RB5009 Router handling forwarded connections from a VPS through Wireguard.

Currently only doing simple port-forwarding. A BGP based load balancer is
planned for the future to automate this process.
