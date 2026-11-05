# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT

# Apply under Safe Mode after taking an encrypted off-device backup.
:if ([:len [/routing/filter/rule/find where comment="ansible:cilium:bgp:v4:reject"]] != 1) do={:error "Expected the existing BGP reject anchor"}
:if ([:len [/ip/firewall/filter/find where comment="Allow dstnat flows to Cilium LoadBalancer IP"]] != 1) do={:error "Expected the existing public forwarding anchor"}

:if ([:len [/routing/filter/rule/find where comment="ops:cilium:private:v4:accept"]] = 0) do={
    /routing/filter/rule/add chain=cilium-bgp-in-v4 rule="if (dst in 172.21.68.11/32) {accept}" comment="ops:cilium:private:v4:accept" place-before=[find where comment="ansible:cilium:bgp:v4:reject"]
}
:if ([:len [/ip/firewall/filter/find where comment="ops:cilium:private:lan"]] = 0) do={
    /ip/firewall/filter/add chain=forward action=accept in-interface=bridge src-address=172.21.69.0/24 dst-address=172.21.68.11 protocol=tcp dst-port=443 comment="ops:cilium:private:lan" place-before=[find where comment="Allow dstnat flows to Cilium LoadBalancer IP"]
}
:if ([:len [/ip/firewall/filter/find where comment="ops:cilium:private:vpn"]] = 0) do={
    /ip/firewall/filter/add chain=forward action=accept in-interface=wireguard-local src-address=172.21.70.0/24 dst-address=172.21.68.11 protocol=tcp dst-port=443 comment="ops:cilium:private:vpn" place-before=[find where comment="Allow dstnat flows to Cilium LoadBalancer IP"]
}
:if ([:len [/ip/firewall/filter/find where comment="ops:cilium:private:nas"]] = 0) do={
    /ip/firewall/filter/add chain=forward action=accept in-interface=vlan71-servers src-address=172.21.71.10/32 dst-address=172.21.68.11 protocol=tcp dst-port=443 comment="ops:cilium:private:nas" place-before=[find where comment="Allow dstnat flows to Cilium LoadBalancer IP"]
}
:if ([:len [/ip/firewall/filter/find where comment="ops:cilium:private:deny"]] = 0) do={
    /ip/firewall/filter/add chain=forward action=drop dst-address=172.21.68.11 comment="ops:cilium:private:deny" place-before=[find where comment="Allow dstnat flows to Cilium LoadBalancer IP"]
}
