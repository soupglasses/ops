<!--
SPDX-FileCopyrightText: 2025 Sofie <sofie+git@mailbox.org>

SPDX-License-Identifier: MIT
-->

# Home Operations 👩‍💻

Just another home operations Talos Linux cluster.

## Overview

Kubernetes cluster is deployed on top of Talos Linux, bootstrapped with

Applications are deployed using FluxCD.

### Network layout

Mikrotik RB5009 Router handling forwarded connections from a VPS through Wireguard.

Currently only doing simple port-forwarding. A BGP based load balancer is
planned for the future to automate this process.

## Configure new cluster

1. **Install Talos Linux** on each control-plane and worker node. Generate `.secure/kubeconfig`, `.secure/talosconfig` using [Talos: Production Notes](https://www.talos.dev/v1.10/introduction/prodnotes/). After install, put each created machine patch into the `talos/` folder for safekeeping. You should also use Talos bootstrap script to set up the kubernetes cluster.
2. **Install FluxCD** following this guide [Flux: Gitea bootstrap](https://fluxcd.io/flux/installation/bootstrap/gitea/). Remember to set `--hostname=codeberg.org` and place the configuration into `kubernetes/clusters/$CLUSTER_NAME`.
3. **Configure the SOPS encryption** using [Flux: Encrypting secrets using age](https://fluxcd.io/flux/guides/mozilla-sops/#encrypting-secrets-using-age). Place the generated key into `.secure/age.agekey`. Ensure you also add the public key into `.sops.yaml` as well.
