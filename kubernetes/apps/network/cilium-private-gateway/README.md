<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: MIT
-->

# Private HTTPS Gateway

Jellyfin uses `https://jellyfin.home.finnes.dev` at private IPv4 VIP
`172.21.68.11`. Cilium advertises this /32 through the existing BGP sessions to
RouterOS, alongside public service VIP `172.21.68.10`. The router must accept both
exact prefixes. The private VIP has no WAN NAT mapping and its firewall permits
only trusted LAN `172.21.69.0/24`, VPN `172.21.70.0/24`, and NAS
`172.21.71.10/32` to TCP 443. Other traffic to the private VIP is denied.

BGP provides reachability; source restrictions and firewall rules enforce private
access. The Gateway has its own class, IPv4-only Service and HTTPS listener. Only
routes from `media` can attach. Its Service repeats the trusted source ranges and
allocates no NodePorts. Neither public Gateway address serves Jellyfin. Shared
Cilium proxy egress permits Jellyfin's backend port, while route attachment and
frontend restrictions determine which listener and clients can reach it.

ExternalDNS publishes a DNS-only A record containing the private address. DNS01
certificate issuance needs no public listener. Publishing a private A record
reveals the address, but does not make it publicly routable. Keep Cloudflare
proxying disabled and never inherit the public Gateway's WAN target annotation.
Jellyfin owns its HTTPRoute under `../../media/jellyfin/app/httproute.yaml`.

## Deployment and recovery

Before allocating the VIP, verify it is absent from router address reservations,
existing Service assignments and BGP routes. Configure the router's exact-prefix
acceptance and private firewall rules before deploying the Service. Preserve the
public VIP's route, NAT and firewall rules. See
[RouterOS operations](../../../../docs/routeros-bgp-config.md).

Flux reconciles the dedicated IP pool, Gateway, certificate and application route.
The pool requires both the private-exposure and BGP labels. Existing BGP
advertisement rules select the generated Service through its BGP label. No additional announcement mechanism is required.

The existing RKE2 Cilium BGP configuration is reused. Flux rebuilds the Gateway,
and cert-manager reissues TLS using the independently recoverable DNS credentials.
Router routing and firewall configuration must remain recoverable independently
of the cluster.

## Verification

```sh
task ansible:gateway:test
task ansible:gateway:verify
```

The read-only live check validates the generated Service's source ranges,
IPv4-only address, disabled NodePorts, BGP class and selection label, exclusive
route attachment, BGP advertisements, trusted TLS and Jellyfin health. It requires
both public IPv4 and IPv6 Gateway addresses to reject Jellyfin's hostname. Run it
after Cilium or router maintenance as well as initial deployment. A healthy
Gateway condition alone does not prove connectivity.

From a trusted LAN or VPN client, test the URL with ordinary certificate
validation. From an untrusted network, verify TCP 443 on the private VIP is denied;
do not infer this from DNS or BGP alone. Verify router drop counters and that no
WAN NAT mapping targets the private VIP. Also check public `auth.finnes.dev` and
`grafana.finnes.dev` over both address families. Jellyfin must have no AAAA record.
The Ansible check runs from NAS and does not establish external source denial by
itself.

If reachability fails, inspect the Cilium advertised routes and RouterOS received
route, exact-prefix filter and firewall counters. Revert faulty settings through
Git and retain router rollback access during changes. Do not enable global
Gateway host networking: Cilium 1.19.6 cannot select it per GatewayClass and it
would replace the public LoadBalancer mode.

References: [Cilium 1.19.6 BGP advertisements](https://github.com/cilium/cilium/blob/v1.19.6/Documentation/network/bgp-control-plane/bgp-control-plane-configuration.rst),
[GatewayClass API](https://github.com/cilium/cilium/blob/v1.19.6/pkg/k8s/apis/cilium.io/v2alpha1/gatewayclassconfig_types.go).
