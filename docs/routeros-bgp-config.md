<!--
SPDX-FileCopyrightText: 2025 Sofie <sofie+git@mailbox.org>

SPDX-License-Identifier: MIT
-->

# RouterOS server VLAN and Cilium BGP configuration

This records the desired RB5009 configuration for native public ingress. It omits
unrelated default, LAN, container, and remote-access WireGuard configuration.
Private keys are never included.

## Server VLAN 71

The dedicated bridge avoids changing VLAN filtering on the trusted-LAN bridge.
`ether8` is an untagged access port; the bridge CPU is tagged so the VLAN interface
can provide routing and DHCP.

```routeros
/interface/bridge
add name=bridge-servers protocol-mode=rstp vlan-filtering=yes \
    comment="Server VLAN bridge"

/interface/vlan
add name=vlan71-servers interface=bridge-servers vlan-id=71 \
    comment="Server VLAN 71 L3 interface"

/interface/bridge/port
add bridge=bridge-servers interface=ether8 pvid=71 ingress-filtering=yes \
    frame-types=admit-only-untagged-and-priority-tagged \
    comment="Server VLAN 71 access port"

/interface/bridge/vlan
add bridge=bridge-servers vlan-ids=71 tagged=bridge-servers untagged=ether8 \
    comment="Server VLAN 71"

/ip/address
add address=172.21.71.1/24 interface=vlan71-servers \
    comment="Server VLAN gateway"
```

For a safe migration, create the bridge with `vlan-filtering=no`, configure its VLAN
interface, VLAN table, address, DHCP, and firewall, move only `ether8`, and then enable
filtering. The main LAN bridge is never changed.

## DHCP

```routeros
/ip/pool
add name=dhcp-servers ranges=172.21.71.100-172.21.71.254

/ip/dhcp-server/network
add address=172.21.71.0/24 gateway=172.21.71.1 dns-server=172.21.71.1 \
    ntp-server=172.21.71.1 domain=home.arpa

/ip/dhcp-server
add name=dhcp-servers interface=vlan71-servers address-pool=dhcp-servers \
    lease-time=6h30m add-dns-entries-suffix=lan use-reconfigure=yes

/ip/dhcp-server/lease
add address=172.21.71.10 mac-address=30:9C:23:82:38:72 \
    server=dhcp-servers comment="nas"
```

The live DHCP server also runs the existing `dhcp-to-dns` lease script. It maintains
`nas.home.arpa`, and `k8s.home.arpa` remains a CNAME to that name.

## BGP

```routeros
/routing/filter/rule
add chain=bgp-input-filter \
    rule="if (dst in 172.21.68.10/32) {accept}" \
    comment="Accept Cilium LoadBalancer IP"
add chain=bgp-input-filter rule="reject" comment="Reject other BGP routes"

/routing/bgp/connection
add name=cilium-bgp remote.address=172.21.71.10 as=64512 \
    local.address=172.21.71.1 local.role=ebgp connect=no listen=yes \
    multihop=yes input.filter=bgp-input-filter \
    comment="Cilium BGP peering - home cluster"
```

Cilium should advertise `172.21.68.10/32` with next hop `172.21.71.10`. RouterOS does
not need the pod CIDR for ingress forwarding, so the filter deliberately rejects it.

## Firewall policy

These input accepts must be before `defconf: drop all not coming from LAN`. The
forward drop must be after established/related acceptance and before later forwarding
accepts.

```routeros
/ip/firewall/filter
add chain=input action=accept in-interface=vlan71-servers protocol=udp \
    dst-port=67 comment="Allow DHCP from server VLAN"
add chain=input action=accept in-interface=vlan71-servers protocol=udp \
    dst-port=53,123 comment="Allow DNS and NTP from server VLAN"
add chain=input action=accept in-interface=vlan71-servers protocol=tcp \
    dst-port=53 comment="Allow DNS from server VLAN"
add chain=input action=accept in-interface=vlan71-servers \
    src-address=172.21.71.10 dst-address=172.21.71.1 protocol=tcp \
    dst-port=179 comment="Allow BGP from Cilium on server VLAN"

add chain=forward action=drop connection-state=new \
    src-address=172.21.71.0/24 dst-address=172.21.69.0/24 \
    comment="Block server VLAN initiating to trusted LAN"
```

The established/related rule allows replies when a trusted-LAN client initiated the
connection. There is intentionally no server-VLAN-to-WAN drop.

## Native public ingress

The UDP exception keeps the direct remote-access WireGuard listener on RouterOS.
Kubernetes services determine which of the translated ports have listeners.

```routeros
/ip/firewall/nat
add chain=dstnat action=dst-nat in-interface=ether1 \
    dst-address=80.248.139.51 protocol=tcp to-addresses=172.21.68.10 \
    comment="Native WAN TCP to Cilium LoadBalancer"
add chain=dstnat action=dst-nat in-interface=ether1 \
    dst-address=80.248.139.51 protocol=udp dst-port=!51821 \
    to-addresses=172.21.68.10 \
    comment="Native WAN UDP to Cilium LoadBalancer except WireGuard"
add chain=dstnat action=dst-nat in-interface-list=LAN \
    dst-address=80.248.139.51 protocol=tcp to-addresses=172.21.68.10 \
    comment="Hairpin native WAN TCP to Cilium LoadBalancer"
add chain=dstnat action=dst-nat in-interface-list=LAN \
    dst-address=80.248.139.51 protocol=udp dst-port=!51821 \
    to-addresses=172.21.68.10 \
    comment="Hairpin native WAN UDP to Cilium LoadBalancer except WireGuard"
add chain=srcnat action=masquerade src-address-list=LAN \
    dst-address=172.21.68.10 comment="Hairpin native WAN ingress masquerade"

/ip/firewall/filter
add chain=input action=accept in-interface=ether1 dst-address=80.248.139.51 \
    protocol=udp dst-port=51821 \
    comment="Allow wireguard-local on native WAN"
add chain=forward action=accept connection-nat-state=dstnat \
    dst-address=172.21.68.10 \
    comment="Allow dstnat flows to Cilium LoadBalancer IP"
```

## Verification

```routeros
/routing/bgp/session/print detail
/ip/route/print detail where dst-address=172.21.68.10/32
/ip/dhcp-server/lease/print detail where address=172.21.71.10
/interface/bridge/vlan/print detail where bridge=bridge-servers
```

Expected route:

```text
172.21.68.10/32 gateway=172.21.71.10%vlan71-servers
```

From outside the home network, verify HTTPS, Minecraft TCP, voice-chat UDP, and
UDP/51821. NAT and filter counters can confirm UDP packets where the application does
not provide a probe response.
