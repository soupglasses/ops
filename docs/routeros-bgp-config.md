<!--
SPDX-FileCopyrightText: 2025 Sofie <sofie+git@mailbox.org>

SPDX-License-Identifier: MIT
-->

# RouterOS Public Ingress Guide

This document describes the live RouterOS setup that makes the public IP work for
the home cluster.

It focuses on the final working design, not older experiments.

## Network Summary

| Item | Value | Purpose |
| --- | --- | --- |
| Public IP | `192.121.119.137` | Address users connect to |
| WireGuard public side | `10.0.0.2/32` | Address RouterOS receives from the VPS tunnel |
| RouterOS BGP address | `172.21.68.1` | Local BGP address on RouterOS |
| Cilium BGP peer | `172.21.69.10` | Kubernetes node IP |
| Public ingress IP | `172.21.68.10/32` | `ingress-nginx-public` LoadBalancer IP |
| RouterOS ASN | `64513` | RouterOS BGP ASN |
| Cilium ASN | `64512` | Cilium BGP ASN |

## What Each Part Does

- `BGP` teaches RouterOS how to reach `172.21.68.10`
- `DNAT` changes incoming public traffic to the ingress service IP
- `VRF` keeps the public `/32` out of the main routing table
- `Connection and routing marks` force replies back through the public tunnel
- `Hairpin NAT` lets LAN devices use the same public IP as outside users

## Quick Runbook

This is the short copy/paste version of the live working setup. It keeps `udp/51821`
on the local WireGuard endpoint, sends almost all other public traffic to Kubernetes,
and keeps replies on the same public path.

```routeros
# Restrict RouterOS management to trusted admin subnets
/ip/service
set ssh address=172.21.69.0/24,172.21.70.0/24
set winbox address=172.21.69.0/24,172.21.70.0/24
set www-ssl address=172.21.69.0/24,172.21.70.0/24

# BGP filters and session
/routing/filter/rule
add chain=bgp-input-filter rule="if (dst in 172.21.68.10/32) {accept}" comment="Accept Cilium LoadBalancer IP"
add chain=bgp-input-filter rule="if (dst in 10.42.0.0/16) {accept}" comment="Accept Cilium Pod CIDR"
add chain=bgp-input-filter rule="reject" comment="Reject other BGP routes"

/routing/bgp/connection
add name=cilium-bgp remote.address=172.21.69.10 as=64512 local.address=172.21.68.1 local.role=ebgp connect=no listen=yes multihop=yes input.filter=bgp-input-filter comment="Cilium BGP peering - home cluster"

# Keep the public /32 out of main
/interface/bridge
add name=lo-public-svc protocol-mode=none comment="public service /32 only"

/ip/vrf
add name=public-svc interfaces=lo-public-svc place-before=0

/ip/address
remove [find where address="192.121.119.137/32" and interface="loopback-public"]
add address=192.121.119.137/32 interface=lo-public-svc comment="public service IP in VRF"

# Public routing table for reply traffic
/routing/table
add disabled=no fib name=public

/ip/route
add disabled=no dst-address=0.0.0.0/0 gateway=wireguard-public routing-table=public

# Keep udp/51821 above the catch-all rules
/ip/firewall/nat
add chain=dstnat action=dst-nat to-addresses=172.21.70.1 protocol=udp in-interface=wireguard-public dst-port=51821 comment="port-forward wireguard-local"

# Public traffic to Kubernetes, except ssh and WireGuard
/ip/firewall/nat
add chain=dstnat action=dst-nat to-addresses=172.21.68.10 protocol=tcp in-interface=wireguard-public dst-address=10.0.0.2 dst-port=!22 comment="Public TCP to LoadBalancer except SSH"
add chain=dstnat action=dst-nat to-addresses=172.21.68.10 protocol=udp in-interface=wireguard-public dst-address=10.0.0.2 dst-port=!51820-51821 comment="Public UDP to LoadBalancer except WireGuard"

# LAN hairpin to the same public IP
/ip/firewall/nat
add chain=srcnat action=masquerade dst-address=172.21.68.10 src-address-list=LAN comment="Hairpin public ingress masquerade"
add chain=dstnat action=dst-nat to-addresses=172.21.68.10 protocol=tcp in-interface-list=LAN dst-address=192.121.119.137 dst-port=!22 comment="Hairpin TCP to LoadBalancer except SSH"
add chain=dstnat action=dst-nat to-addresses=172.21.68.10 protocol=udp in-interface-list=LAN dst-address=192.121.119.137 dst-port=!51820-51821 comment="Hairpin UDP to LoadBalancer except WireGuard"

# Force replies back through the public tunnel
/ip/firewall/mangle
add chain=prerouting action=mark-connection new-connection-mark=wg-public passthrough=yes connection-state=new in-interface=wireguard-public comment="Mark WG-public connections"
add chain=prerouting action=mark-routing new-routing-mark=public passthrough=no connection-mark=wg-public in-interface=!wireguard-public comment="Route WG-public return traffic via public table"
add chain=output action=mark-routing new-routing-mark=public passthrough=no dst-address-type=!local connection-mark=wg-public comment="Route WG-public local replies via public table"

# Restrict BGP to the expected peer only
/ip/firewall/filter
add chain=input action=accept protocol=tcp src-address=172.21.69.10 dst-address=172.21.68.1 dst-port=179 comment="Allow BGP only from Cilium peer"
add chain=input action=drop protocol=tcp dst-address=172.21.68.1 dst-port=179 comment="Drop other BGP to RouterOS"

# Keep public flows away from fasttrack and narrow service forwarding
/ip/firewall/filter
add chain=forward action=accept connection-mark=wg-public comment="WG-public: accept before fasttrack"
add chain=forward action=accept connection-nat-state=dstnat dst-address=172.21.68.10 comment="Allow dstnat flows to Cilium LoadBalancer IP"
add chain=forward action=accept src-address=172.21.69.0/24 dst-address=172.21.68.10 comment="Allow LAN direct access to Cilium LoadBalancer IP"
add chain=forward action=accept src-address=172.21.70.0/24 dst-address=172.21.68.10 comment="Allow wireguard-local direct access to Cilium LoadBalancer IP"
```

## 1. Restrict RouterOS Management Access

These services should only be reachable from the trusted LAN and local WireGuard
subnets.

```bash
/ip/service
set ssh address=172.21.69.0/24,172.21.70.0/24
set winbox address=172.21.69.0/24,172.21.70.0/24
set www-ssl address=172.21.69.0/24,172.21.70.0/24
```

Why this matters:

- firewall rules are still the first line of defense
- service-level address filters protect you if interface-list or input rules drift later

## 2. BGP: Teach RouterOS Where the Service Lives

RouterOS listens for a BGP session from Cilium and accepts only the routes it
needs.

```bash
/routing/bgp/connection
add name=cilium-bgp \
  remote.address=172.21.69.10 \
  as=64512 \
  local.address=172.21.68.1 \
  local.role=ebgp \
  connect=no \
  listen=yes \
  multihop=yes \
  input.filter=bgp-input-filter \
  comment="Cilium BGP peering - home cluster"

/routing/filter/rule
add chain=bgp-input-filter \
  rule="if (dst in 172.21.68.10/32) {accept}" \
  comment="Accept Cilium LoadBalancer IP"

/routing/filter/rule
add chain=bgp-input-filter \
  rule="if (dst in 10.42.0.0/16) {accept}" \
  comment="Accept Cilium Pod CIDR"

/routing/filter/rule
add chain=bgp-input-filter \
  rule="reject" \
  comment="Reject other BGP routes"
```

Why this matters:

- RouterOS learns `172.21.68.10/32 -> 172.21.69.10`
- only the expected service and pod routes are accepted

## 3. Keep the Public /32 Out of the Main Table

The public IP must not live in the main routing table, or RouterOS can treat the
WireGuard peer endpoint as itself.

```bash
/interface/bridge
add name=lo-public-svc protocol-mode=none comment="public service /32 only"

/ip/vrf
add name=public-svc interfaces=lo-public-svc place-before=0

/ip/address
remove [find where address="192.121.119.137/32" and interface="loopback-public"]
add address=192.121.119.137/32 interface=lo-public-svc comment="public service IP in VRF"
```

Why this matters:

- `192.121.119.137` still exists as the public service IP
- RouterOS stops routing the WireGuard peer to itself

## 4. Public DNAT: Send Tunnel Traffic to Ingress

The VPS already forwards public traffic into WireGuard, so RouterOS sees the
destination as `10.0.0.2`, not `192.121.119.137`.

```bash
/ip/firewall/nat
add chain=dstnat \
  in-interface=wireguard-public \
  dst-address=10.0.0.2 \
  protocol=tcp \
  dst-port=!22 \
  action=dst-nat \
  to-addresses=172.21.68.10 \
  comment="Public TCP to LoadBalancer except SSH"

/ip/firewall/nat
add chain=dstnat \
  in-interface=wireguard-public \
  dst-address=10.0.0.2 \
  protocol=udp \
  dst-port=!51820-51821 \
  action=dst-nat \
  to-addresses=172.21.68.10 \
  comment="Public UDP to LoadBalancer except WireGuard"
```

Why this matters:

- incoming public traffic lands on the ingress service IP
- the rules match the real tunneled destination
- RouterOS does not send TCP `22` or UDP `51820-51821` into Kubernetes

The dedicated UDP `51821` forward must stay above these catch-all rules.

## 5. Return Path: Force Replies Back Through the Public Tunnel

The reply must leave the same public path it arrived on.

```bash
/routing/table
add disabled=no fib name=public

/ip/route
add disabled=no dst-address=0.0.0.0/0 gateway=wireguard-public routing-table=public

/ip/firewall/mangle
add chain=prerouting \
  in-interface=wireguard-public \
  connection-state=new \
  action=mark-connection \
  new-connection-mark=wg-public \
  passthrough=yes \
  comment="Mark WG-public connections"

/ip/firewall/mangle
add chain=prerouting \
  connection-mark=wg-public \
  in-interface=!wireguard-public \
  action=mark-routing \
  new-routing-mark=public \
  passthrough=no \
  comment="Route WG-public return traffic via public table"

/ip/firewall/mangle
add chain=output \
  connection-mark=wg-public \
  dst-address-type=!local \
  action=mark-routing \
  new-routing-mark=public \
  passthrough=no \
  comment="Route WG-public local replies via public table"
```

Why this matters:

- replies do not leak out the ISP default route
- the public session stays symmetric and stable

## 6. Firewall Rules Needed for the Working Path

```bash
/ip/firewall/filter
add chain=forward \
  connection-mark=wg-public \
  action=accept \
  comment="WG-public: accept before fasttrack"

/ip/firewall/filter
add chain=input \
  action=accept \
  protocol=tcp \
  src-address=172.21.69.10 \
  dst-address=172.21.68.1 \
  dst-port=179 \
  comment="Allow BGP only from Cilium peer"

/ip/firewall/filter
add chain=input \
  action=drop \
  protocol=tcp \
  dst-address=172.21.68.1 \
  dst-port=179 \
  comment="Drop other BGP to RouterOS"

/ip/firewall/filter
add chain=forward \
  connection-nat-state=dstnat \
  dst-address=172.21.68.10 \
  action=accept \
  comment="Allow dstnat flows to Cilium LoadBalancer IP"

/ip/firewall/filter
add chain=forward \
  src-address=172.21.69.0/24 \
  dst-address=172.21.68.10 \
  action=accept \
  comment="Allow LAN direct access to Cilium LoadBalancer IP"

/ip/firewall/filter
add chain=forward \
  src-address=172.21.70.0/24 \
  dst-address=172.21.68.10 \
  action=accept \
  comment="Allow wireguard-local direct access to Cilium LoadBalancer IP"
```

Why this matters:

- public-side flows are accepted before fasttrack can bypass mangle
- only DNATed public flows and the two trusted local subnets get direct access
- BGP listeners are no longer reachable from arbitrary local sources

## 7. Hairpin NAT for Local Clients

LAN devices should be able to open the same public IP as outside users.

```bash
/ip/firewall/nat
add chain=dstnat \
  in-interface-list=LAN \
  dst-address=192.121.119.137 \
  protocol=tcp \
  dst-port=!22 \
  action=dst-nat \
  to-addresses=172.21.68.10 \
  comment="Hairpin TCP to LoadBalancer except SSH"

/ip/firewall/nat
add chain=dstnat \
  in-interface-list=LAN \
  dst-address=192.121.119.137 \
  protocol=udp \
  dst-port=!51820-51821 \
  action=dst-nat \
  to-addresses=172.21.68.10 \
  comment="Hairpin UDP to LoadBalancer except WireGuard"

/ip/firewall/nat
add chain=srcnat \
  src-address-list=LAN \
  dst-address=172.21.68.10 \
  action=masquerade \
  comment="Hairpin public ingress masquerade"
```

Why this matters:

- local devices can use the public IP and public hostname
- replies still come back through RouterOS instead of bypassing it
- local traffic follows the same port policy as public traffic

## 8. MSS Clamp for the Public Tunnel

The public WireGuard path may need MSS clamping for stable TCP traffic.

```bash
/ip/firewall/mangle
add chain=forward \
  protocol=tcp \
  tcp-flags=syn \
  in-interface=wireguard-public \
  action=change-mss \
  new-mss=clamp-to-pmtu \
  comment="Clamp MSS for WG-public ingress"

/ip/firewall/mangle
add chain=forward \
  protocol=tcp \
  tcp-flags=syn \
  out-interface=wireguard-public \
  action=change-mss \
  new-mss=clamp-to-pmtu \
  comment="Clamp MSS for WG-public egress"
```

## Verification

Check these in order.

### 1. WireGuard is alive

```bash
/interface/wireguard/peers/print detail where name="peer-public"
```

You want to see a recent handshake and increasing `rx` and `tx`.

### 2. BGP is established

```bash
/routing/bgp/session/print detail
```

You want to see:

- `established`
- `local.address=172.21.68.1`
- route count greater than zero

### 3. RouterOS management is restricted

```bash
/ip/service/print detail where name="ssh" or name="winbox" or name="www-ssl"
```

You want to see `address=172.21.69.0/24,172.21.70.0/24` on all three services.

### 4. RouterOS learned the ingress route

```bash
/routing/route/print where bgp
```

You want to see `172.21.68.10/32` pointing at `172.21.69.10`.

### 5. Public DNAT is being hit

```bash
/ip/firewall/nat/print stats where comment~"Public .*LoadBalancer|Hairpin .*LoadBalancer"
```

The packet counter should increase during outside tests.

### 6. Return-path marks are being hit

```bash
/ip/firewall/mangle/print stats where comment~"WG-public"
```

The mark counters should move when public traffic is active.

### 7. Local hairpin works

Test from the LAN:

```bash
curl -I http://192.121.119.137
```

## Troubleshooting Order

If the public path breaks, check in this order:

1. does traffic arrive on `wireguard-public`
2. does the public DNAT counter move
3. does BGP still advertise `172.21.68.10/32`
4. do the routing-mark counters move on replies
5. do the BGP input rule and service address restrictions still match the intended sources
6. does LAN access hit the hairpin NAT rules

## Final Working Shape

The final working path is:

```text
Public IP on VPS
  -> WireGuard tunnel
  -> 10.0.0.2 on RouterOS
  -> DNAT to 172.21.68.10
  -> BGP route to 172.21.69.10
  -> ingress-nginx-public
  -> reply marked into routing table public
  -> back out wireguard-public
```
