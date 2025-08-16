<!--
SPDX-FileCopyrightText: 2025 Sofie <sofie+git@mailbox.org>

SPDX-License-Identifier: MIT
-->

# 👩‍💻 Infra 👩‍💻

Just another home operations Talos Linux cluster.

## Overview

- **OS**: [Talos Linux](https://www.talos.dev/)
- **Cluster**: Kubernetes, bootstrapped following [Talos Production Notes](https://www.talos.dev/v1.10/introduction/prodnotes/)
- **GitOps**: Flux CD
- **Networking**: Flannel CNI
- **Storage**: OpenEBS ZFS

### Network layout

Mikrotik RB5009 Router handling forwarded connections from a VPS through Wireguard.

Currently only doing simple port-forwarding. A BGP based load balancer is
planned for the future to automate this process.
