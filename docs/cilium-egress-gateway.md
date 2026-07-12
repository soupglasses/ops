<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# Cilium egress gateway (idea, not implemented)

Goal: route selected pods (label `egress.home.arpa/gateway: public`) out through a
dedicated egress IP instead of the node's default SNAT, so their traffic can be matched
by RouterOS rules (e.g. sent out the public VPS path).

A draft policy existed as an untracked file in `kubernetes/apps/`; preserved here because
it cannot work as written. What is missing before this is real:

1. Cilium must be installed with `egressGateway.enabled=true` (and `bpf.masquerade=true`).
   Today Cilium is installed by the inline manifest job in `talos/patches/cilium.yaml`,
   so this is a machine-config change until Cilium moves into Flux.
2. The node selector label was wrong: use `kubernetes.io/hostname: nas`.
3. The egress IP (draft used `172.43.1.10`) must exist on an interface of the node and be
   covered by RouterOS routing/filters. Pick it deliberately alongside the addressing in
   `docs/network-topology.md`.

Corrected shape of the policy:

```yaml
apiVersion: cilium.io/v2
kind: CiliumEgressGatewayPolicy
metadata:
  name: public
spec:
  selectors:
    - podSelector:
        matchLabels:
          egress.home.arpa/gateway: public
  destinationCIDRs:
    - "0.0.0.0/0"
  egressGateway:
    nodeSelector:
      matchLabels:
        kubernetes.io/hostname: nas
    egressIP: 172.43.1.10
```

When implemented, this belongs as a proper app under `kubernetes/apps/network/`
(same pattern as `cilium-bgp`).
