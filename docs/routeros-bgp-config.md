<!--
SPDX-FileCopyrightText: 2025 Sofie <sofie+git@mailbox.org>

SPDX-License-Identifier: MIT
-->

# RouterOS server VLAN and Cilium BGP configuration

This records the desired RB5009 configuration for native public ingress. It omits
unrelated default, LAN, container, and remote-access WireGuard configuration.
Private keys are never included.

## Native IPv6

The ISP routes `2a13:e745:1ee8::/48` to the static WAN address. Each local network
gets a `/64` whose subnet ID follows the corresponding IPv4 network or VLAN number.
The WireGuard range remains reserved. Kubernetes uses
`2a13:e745:1ee8:68::10/128` as its shared public service VIP.

```routeros
/ipv6/address
add address=2a13:e740:285:893a::2/64 interface=ether1 advertise=no \
    comment="ISP static IPv6 WAN"
add address=2a13:e745:1ee8:69::1/64 interface=bridge advertise=yes \
    comment="Trusted LAN IPv6"
add address=2a13:e745:1ee8:71::1/64 interface=vlan71-servers advertise=yes \
    comment="Server VLAN 71 IPv6"

/ipv6/route
add dst-address=::/0 gateway=2a13:e740:285:893a::1%ether1 distance=1 \
    comment="ISP static IPv6 default"

/ipv6/nd
set 0 interface=bridge advertise-dns=self
add interface=vlan71-servers advertise-dns=self
```

Router advertisements run only on the trusted bridge and server VLAN, never the WAN.
RDNSS points clients at the RouterOS DNS resolver. The active reservations are:

```text
2a13:e745:1ee8:68::10/128  Kubernetes public service VIP
2a13:e745:1ee8:70::/64  remote-access WireGuard
```

The server VLAN is not a member of the broad `LAN` interface list. Add narrow rules
before the default non-LAN drops so it can use RouterOS DNS and reach the Internet
without gaining access to the trusted LAN. The isolation drop precedes the default
broad ICMPv6 forward accept.

```routeros
/ipv6/firewall/filter
add chain=input action=accept protocol=udp dst-port=53 \
    in-interface=vlan71-servers \
    place-before=[find where chain=input in-interface-list=!LAN action=drop] \
    comment="Allow IPv6 DNS UDP from server VLAN"
add chain=input action=accept protocol=tcp dst-port=53 \
    in-interface=vlan71-servers \
    place-before=[find where chain=input in-interface-list=!LAN action=drop] \
    comment="Allow IPv6 DNS TCP from server VLAN"
add chain=forward action=accept connection-state=new \
    in-interface=vlan71-servers out-interface-list=WAN \
    place-before=[find where chain=forward in-interface-list=!LAN action=drop] \
    comment="Allow server VLAN IPv6 internet egress"
add chain=forward action=drop connection-state=new \
    in-interface=vlan71-servers out-interface=bridge \
    place-before=[find where chain=forward protocol=icmpv6 action=accept] \
    comment="Block server VLAN IPv6 to trusted LAN"
```

Verification:

```routeros
/ipv6/address/print detail where global
/ipv6/route/print detail
/ipv6/nd/print detail
/ipv6/nd/prefix/print detail
/ipv6/firewall/filter/print stats
```

## DNS observability

RouterOS is distributed as the resolver through IPv4 DHCP and IPv6 RDNSS, but client
DNS is not destination-NATed. A passthrough rule after FastTrack counts new direct
IPv4 UDP/53 flows without accepting, dropping, or redirecting them. Router-local DNS
does not traverse the forward chain and is therefore excluded.

```routeros
/ip/firewall/filter
add chain=forward action=passthrough connection-state=new protocol=udp \
    dst-port=53 dst-address-type=!local in-interface-list=!WAN \
    out-interface-list=WAN \
    place-before=[find where comment="defconf: accept established,related, untracked"] \
    comment="Count direct outbound DNS UDP"
```

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
add chain=cilium-bgp-in-v4 \
    rule="if (dst in 172.21.68.10/32) {accept}" \
    comment="Accept Cilium LoadBalancer IP"
add chain=cilium-bgp-in-v4 rule="reject" comment="Reject other Cilium IPv4 routes"
add chain=cilium-bgp-in-v6 \
    rule="if (dst in 2a13:e745:1ee8:68::10/128) {accept}" \
    comment="Accept Cilium IPv6 LoadBalancer IP"
add chain=cilium-bgp-in-v6 rule="reject" comment="Reject other Cilium IPv6 routes"

/routing/bgp/connection
add name=cilium-bgp instance=bgp-instance-1 remote.address=172.21.71.10 remote.as=64512 \
    local.address=172.21.71.1 local.role=ebgp connect=no listen=yes \
    multihop=no input.filter=cilium-bgp-in-v4 \
    comment="Cilium BGP peering - home cluster"
add name=cilium-bgp-v6 instance=bgp-instance-1 remote.address=2a13:e745:1ee8:71::10 remote.as=64512 \
    local.address=2a13:e745:1ee8:71::1 local.role=ebgp connect=no listen=yes \
    multihop=no input.filter=cilium-bgp-in-v6 \
    comment="Cilium IPv6 BGP peering - home cluster"
```

Cilium advertises `172.21.68.10/32` and `2a13:e745:1ee8:68::10/128` with their matching
node-address next hops. RouterOS does not need either pod CIDR for ingress forwarding,
so the filters deliberately reject every other route.

The Ansible role also configures distinct TCP-MD5 passwords on both sessions. They come only from
`ansible/inventories/home/group_vars/all/secrets.sops.yaml` and are intentionally absent from the
command examples above. Cilium reads the identical values from the `password` keys in
`cilium-bgp-ipv4-auth` and `cilium-bgp-ipv6-auth` through each peer's `authSecretRef`.

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

/ipv6/firewall/filter
add chain=input action=accept in-interface=vlan71-servers \
    src-address=2a13:e745:1ee8:71::10 dst-address=2a13:e745:1ee8:71::1 \
    protocol=tcp dst-port=179 comment="Allow IPv6 BGP from Cilium"
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

/ipv6/firewall/filter
add chain=forward action=accept in-interface=ether1 \
    dst-address=2a13:e745:1ee8:68::10 protocol=tcp dst-port=80,443,25565 \
    comment="Allow public IPv6 TCP services to Cilium"
add chain=forward action=accept in-interface=ether1 \
    dst-address=2a13:e745:1ee8:68::10 protocol=udp dst-port=24454 \
    comment="Allow public IPv6 UDP services to Cilium"
```

## Verification

```routeros
/routing/bgp/session/print detail
/ip/route/print detail where dst-address=172.21.68.10/32
/ipv6/route/print detail where dst-address=2a13:e745:1ee8:68::10/128
/ip/dhcp-server/lease/print detail where address=172.21.71.10
/interface/bridge/vlan/print detail where bridge=bridge-servers
```

Expected route:

```text
172.21.68.10/32 gateway=172.21.71.10%vlan71-servers
2a13:e745:1ee8:68::10/128 gateway=2a13:e745:1ee8:71::10%vlan71-servers
```

From outside the home network, verify HTTPS, Minecraft TCP, voice-chat UDP, and
UDP/51821. NAT and filter counters can confirm UDP packets where the application does
not provide a probe response.

## Ansible management boundary

The RouterOS bootstrap role assigns the stable controller `/32` to the automation user's `address`
property before enabling it. Confirm the address is reserved or static before bootstrap. Firewall
rules cannot match RouterOS usernames, so the role preserves global SSH policy and the independent
admin/WireGuard recovery path instead of adding a router-wide drop. The automation group has only
`ssh,read,write,test`; broad `write` is a RouterOS limitation and is constrained operationally by
exact selectors and `ansible:cilium:*` comments. Key custody, immediate revocation, staged key
rotation, BGP password rotation, rollback, and smoke tests are documented in `ansible/README.md`.
