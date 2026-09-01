<!--
SPDX-FileCopyrightText: 2025 Sofie <sofie+git@mailbox.org>

SPDX-License-Identifier: MIT
-->

# Network topology

## Current design

```text
Internet
  -> 80.248.139.51 on RB5009 ether1
  -> RouterOS destination NAT
  -> 172.21.68.10/32 Cilium LoadBalancer VIP
  -> BGP next hop 172.21.71.10
  -> Kubernetes service
```

| Purpose | Address or VLAN |
| --- | --- |
| Public WAN | `80.248.139.51/24`, DHCP reservation from the ISP |
| Public IPv6 WAN | `2a13:e740:285:893a::2/64`, static; gateway `2a13:e740:285:893a::1` |
| Routed IPv6 allocation | `2a13:e745:1ee8::/48` |
| Reserved Kubernetes IPv6 VIP subnet | `2a13:e745:1ee8:68::/64` |
| Trusted LAN | `172.21.69.0/24` |
| Router on trusted LAN | `172.21.69.1` |
| Trusted LAN IPv6 | `2a13:e745:1ee8:69::/64`, SLAAC; router `::1` |
| Remote-access WireGuard | `172.21.70.0/24`, UDP `51821` |
| Reserved WireGuard IPv6 subnet | `2a13:e745:1ee8:70::/64` |
| Server VLAN | VLAN 71, `172.21.71.0/24` |
| Router on server VLAN | `172.21.71.1` |
| Server VLAN IPv6 | `2a13:e745:1ee8:71::/64`, SLAAC; router `::1` |
| Kubernetes node | `172.21.71.10` |
| Public Cilium VIP | `172.21.68.10/32` |
| Router ASN | `64513` |
| Cilium ASN | `64512` |

The public address belongs to RouterOS, so it cannot also be assigned to Cilium.
RouterOS maps it to the private LoadBalancer VIP with destination NAT. Cilium advertises
the VIP over BGP, which lets RouterOS route the translated traffic to the node without
exposing the node address itself.

## Server VLAN

`ether8` is an untagged access port for VLAN 71. It belongs to the dedicated
`bridge-servers`, not the trusted-LAN bridge. VLAN filtering is enabled only on this
server bridge, so changes to it do not affect the access point or normal LAN ports.

The node remains a DHCP client. RouterOS reserves `172.21.71.10` for its known MAC.
This keeps the initial design simple while ensuring BGP and Kubernetes see a stable
node address. A future managed switch can replace the access-port configuration with
a tagged VLAN 71 trunk.

The forwarding policy is intentionally asymmetric:

- trusted LAN and remote-access WireGuard clients may initiate connections to servers;
- replies to those connections are accepted by connection tracking;
- the server VLAN may use the internet;
- new server-VLAN connections to `172.21.69.0/24` are dropped;
- destination-NATed WAN traffic is accepted only toward the Cilium VIP.

The IPv6 policy mirrors this without NAT:

- trusted-LAN devices use SLAAC and may initiate Internet connections;
- the server VLAN uses its own `/64`, may initiate Internet connections, and cannot
  initiate connections to the trusted LAN;
- unsolicited WAN forwarding remains denied;
- RouterOS advertises itself as DNS through RDNSS on the trusted LAN and server VLAN;
- the WireGuard and Kubernetes VIP `/64`s are reserved but not yet assigned.

The ISP also offers DHCPv6-PD, but it delegated only
`2a13:e745:1ee8:3700::/56`. The static configuration was tested using a source
address from the far end of the routed `/48`, confirming that the full allocation is
routed to the static WAN address.

DHCP and RDNSS advertise RouterOS as the preferred DNS resolver, which forwards
queries through certificate-verified DNS over HTTPS. DNS is not intercepted: clients
that deliberately use another IPv4 or IPv6 resolver may reach it directly. A passive
IPv4 firewall counter records new forwarded UDP/53 flows to non-local destinations
without changing their verdict.

## Public ingress

RouterOS sends all WAN TCP traffic to `172.21.68.10`. It sends all WAN UDP traffic to
the same VIP except UDP `51821`, which remains the router's direct remote-access
WireGuard listener. Kubernetes, rather than RouterOS, decides which ports actually
have public listeners.

LAN hairpin rules translate connections to `80.248.139.51` to the same VIP. The
current hairpin source masquerade is retained for simplicity.

Current public listeners sharing the VIP are:

- ingress-nginx-public: TCP `80` and `443`;
- Minecraft: TCP `25565`;
- Minecraft voice chat: UDP `24454`.

## BGP

Cilium peers with RouterOS at `172.21.71.1`. RouterOS accepts the public service VIP
and installs:

```text
172.21.68.10/32 -> 172.21.71.10
```

Pod CIDRs are not advertised to RouterOS. Pod routing is internal to the cluster and
is not required for public VIP forwarding.

## Retired path

The previous public path used `192.121.119.137` on a VPS and an outer WireGuard
tunnel to RouterOS. It is not part of the current design. The separate
`wireguard-local` remote-access network remains in service directly on the home WAN.
See `../ip-forwarding.md` for retirement details.
