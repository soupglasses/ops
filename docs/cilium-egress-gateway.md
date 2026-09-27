<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# Cilium outbound routing

The cluster does not configure a dedicated Cilium egress gateway. There are no
`CiliumEgressGatewayPolicy` resources in the managed manifests. Cilium uses eBPF
masquerading for pod traffic leaving the cluster, with RouterOS providing the
upstream route through the home WAN.

Cilium's installed configuration comes from the Ansible-managed
[`rke2-cilium` HelmChartConfig](../ansible/roles/rke2/templates/rke2-cilium-config.yaml.j2).
RKE2 owns the Cilium installation; Flux owns the post-bootstrap network resources.

The deployed [public Cilium Gateway](../kubernetes/apps/network/cilium-gateway/README.md)
handles incoming HTTP and HTTPS traffic. It is separate from selecting an outbound
source address for application pods. See [network topology](network-topology.md)
for WAN routing, service VIPs and the retired VPS tunnel.

The earlier dedicated-egress proposal targeted that retired VPS path. Its sample
address and Talos configuration instructions are no longer applicable. If dedicated
application egress becomes necessary, define the required destination routes, source
address, node ownership and RouterOS policy before adding an egress gateway.
