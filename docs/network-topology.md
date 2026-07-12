<!--
SPDX-FileCopyrightText: 2025 Sofie <sofie+git@mailbox.org>

SPDX-License-Identifier: MIT
-->

# Network Topology

This document explains how public traffic reaches the home cluster.

The goal is simple:

- the internet reaches `192.121.119.137`
- the VPS forwards that traffic through WireGuard to the router
- the router sends it to the Kubernetes ingress service
- replies go back through the same public path

## What Lives Where

| Item | Address | What it is |
| --- | --- | --- |
| Public IP | `192.121.119.137` | The public address users connect to |
| Router WireGuard public side | `10.0.0.2` | The address RouterOS receives from the VPS tunnel |
| Router BGP address | `172.21.68.1` | The address RouterOS uses for BGP with Cilium |
| Kubernetes node | `172.21.69.10` | The node running Cilium BGP |
| Public ingress service | `172.21.68.10` | The LoadBalancer IP used by `ingress-nginx-public` |
| LAN | `172.21.69.0/24` | Local home network |
| Local WireGuard | `172.21.70.0/24` | Private WireGuard network |

## One Packet Walk

This is the normal public path:

```text
Internet client
  -> 192.121.119.137 on the VPS
  -> forwarded through WireGuard
  -> 10.0.0.2 on RouterOS wireguard-public
  -> RouterOS dst-nat to 172.21.68.10
  -> Kubernetes ingress service
  -> reply goes back through RouterOS
  -> RouterOS forces the reply back into the public table
  -> reply leaves through wireguard-public
```

## Why BGP Is Used

RouterOS needs to know where `172.21.68.10` lives.

Cilium tells RouterOS that route over BGP:

- Cilium side: `172.21.69.10`
- RouterOS side: `172.21.68.1`
- RouterOS learns: `172.21.68.10/32 -> 172.21.69.10`

Without BGP, RouterOS would not know how to reach the public ingress service IP.

## Why the Public /32 Uses Its Own VRF

RouterOS also needs to talk to the WireGuard peer endpoint for the public path.

If `192.121.119.137/32` lives in the main routing table, RouterOS can think that
the public IP belongs to itself and try to route the tunnel endpoint locally.

To avoid that, `192.121.119.137/32` is placed on its own loopback interface in a
small VRF:

- it stays available as the public service IP
- it does not pollute the main routing table
- RouterOS no longer routes the WireGuard peer to itself

## Why Return Traffic Needs Marks

The first packet comes in through `wireguard-public`, but the reply is created by
the cluster.

If RouterOS uses its normal default route for the reply, the packet can leave via
the ISP instead of going back through the VPS tunnel.

That breaks the session.

RouterOS fixes this by:

- marking new public-side connections when they arrive on `wireguard-public`
- marking the reply packets to use the `public` routing table
- sending those replies back out through `wireguard-public`

## Why LAN Clients Also Need Hairpin NAT

Devices on the home network should be able to use the same public IP and hostname
as internet clients.

That is why RouterOS also has:

- a LAN-side dst-nat rule for `192.121.119.137`
- a matching masquerade rule so replies still come back through RouterOS

Without those rules, local clients may bypass the router on the reply path and the
connection can fail.

## Live Working Design

The live design is:

1. VPS forwards public traffic to `10.0.0.2` through WireGuard
2. RouterOS receives that traffic on `wireguard-public`
3. RouterOS sends almost all TCP and UDP traffic to `172.21.68.10`
4. TCP `22` and UDP `51820-51821` are excluded from that catch-all behavior
5. Cilium advertises `172.21.68.10/32` over BGP
6. RouterOS forwards the packet to the cluster node
7. RouterOS forces the reply back through the public routing table
8. LAN clients use separate hairpin NAT rules for the same destination

## Quick Glossary

| Term | Plain-English meaning |
| --- | --- |
| `DNAT` | Change the destination address before forwarding the packet |
| `BGP` | Teach RouterOS which next hop should be used for a service IP |
| `VRF` | A separate routing space used to keep one address out of the main table |
| `Hairpin NAT` | Let local devices use the same public IP as outside users |
| `Routing mark` | Tell RouterOS to use a different routing table for a packet |

## If Something Breaks

Check in this order:

1. does traffic reach `10.0.0.2` on `wireguard-public`
2. do the public dst-nat counters move
3. does RouterOS have `172.21.68.10/32` from BGP
4. do the routing-mark counters move for replies
5. does local access use the hairpin NAT rules
