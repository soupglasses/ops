<!--
SPDX-FileCopyrightText: 2025 Sofie <sofie+git@mailbox.org>

SPDX-License-Identifier: MIT
-->

# tofu-controller gRPC Hang Issue

## Symptoms

- Terraform resource stuck at `Status: Initializing`
- Controller logs show `setting up terraform` then nothing
- Runner pod shows only `Starting the runner... version  sha`

## Environment

- tofu-controller v0.16.2
- tf-runner v0.16.2 (official image)
- Cilium CNI
- Flux v2.x with restrictive NetworkPolicies

## Verified Working

Network connectivity from runner to source-controller:

```bash
# From runner pod - returns 404 (connection works, endpoint doesn't exist)
kubectl exec -n identity ldap-system-users-tf-runner -- \
  wget -qO- --timeout=10 http://source-controller.flux-system.svc.cluster.local/health

# Direct pod connectivity works
kubectl exec -n identity ldap-system-users-tf-runner -- \
  nc -zv -w5 10.244.0.51 9090
# Output: open
```

Network connectivity from tofu-system to runner:

```bash
kubectl run debug-nc --image=busybox -n tofu-system --rm -it --restart=Never -- \
  nc -zv -w5 <runner-pod-ip> 30000
# Output: open
```

TLS secret correctly configured:

```bash
kubectl get pod -n identity ldap-system-users-tf-runner \
  -o jsonpath='{.metadata.labels}' | jq -r '.["tf.weave.works/tls-secret-name"]'
# Returns: terraform-runner.tls-XXXXXXX (secret exists)
```

## Issue

Despite all connectivity working, gRPC communication hangs. Controller calls
`setting up terraform` via gRPC to runner, but runner never receives the call.

## Configuration Applied

```yaml
# tofu-controller HelmRelease values
allowCrossNamespaceRefs: true
usePodSubdomainResolution: true
runner:
  serviceAccount:
    allowedNamespaces: [flux-system, identity]
```

## Status

Unresolved - may be tofu-controller bug or gRPC/TLS handshake issue.

---

It might be that we are working on a Talos Cluster which has more restrictive settings for flux-system.

<https://flux-iac.github.io/tofu-controller/getting_started/> are handy for a howto setup.

Ensure that the versions match.

Im guessing it could be that notification controller is not accessible?

Move the Terraform into flux-system instead of trying to make it work inside of the identity namespace. This would also remove the need for the firewall bypass we have now. This is also what the getting started uses, so would be the best strategy long term i feel.

Ignore having the `tofu` folder locally usable. Explain use of <https://flux-iac.github.io/tofu-controller/tfctl/> instead.
