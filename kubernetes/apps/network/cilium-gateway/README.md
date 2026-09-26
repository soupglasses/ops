<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# Public gateway

Cilium handles HTTP/TLS routing through Gateway API and TCP/UDP load balancing
through Kubernetes Services. The RKE2 bundle supplies Cilium 1.19.6, paired with
Gateway API v1.4.1. Upgrade these together according to Cilium's compatibility
matrix.

| Endpoint | Backend | Exposure |
| --- | --- | --- |
| `auth.finnes.dev` HTTPS | `identity/canaille:80` | Public Gateway |
| `grafana.finnes.dev` HTTPS | `monitoring/victoria-metrics-grafana:80` | Public Gateway |
| `mc.finnes.dev:25565` TCP | `default/minecraft:25565` | Cilium LoadBalancer |
| `mc.finnes.dev:24454` UDP | Minecraft voice chat | Same LoadBalancer |
| `openldap.identity.svc:636` TLS | OpenLDAP | Private ClusterIP |
| `minecraft-rcon.default.svc:25575` TCP | Minecraft RCON | Private ClusterIP |

The shared VIP is `172.21.68.10` and `2a13:e745:1ee8:68::10`. Public IPv4 DNS
points at the router's WAN address, which forwards traffic to the VIP. Public IPv6
DNS points directly at the VIP. Sharing Services use `Cluster` traffic policy.
The Gateway exposes ports 80/443 without NodePorts or host networking. Minecraft
uses native Services because this Cilium version does not implement TCPRoute or
UDPRoute.

## Routing and policy

`app/gateway.yaml` defines the GatewayClass, service settings, certificate and
listeners. `app/routes.yaml` redirects HTTP to HTTPS and attaches backend routes
from the identity and monitoring namespaces. TLS uses the existing DNS01
ClusterIssuer. Authentication remains in each application, with WebSocket upgrades
supported by Cilium's HTTP proxy.

ExternalDNS watches Services and HTTPRoutes. The Gateway target annotation keeps
public DNS pointed at the public addresses, rather than the private IPv4 VIP.
Preserve its TXT ownership settings when changing routes or controllers.

The ingress proxy's egress policy permits only the published backend ports.
Frontend policies admit the Cilium ingress identity, host probes and monitoring.
Application egress remains unchanged. Adding an endpoint requires its certificate
hostname, HTTPRoute and matching backend policy allowance in the same change.

The host policy preserves SSH and Kubernetes API access on management addresses
`172.21.71.10` and `2a13:e745:1ee8:71::10`. Neither port is published on the service
VIP. Run `task ansible:firewall:verify` after network maintenance to check fresh
connections over both families, BGP peers and policy enforcement.

## Logs

Native Envoy JSON access logs go to stdout and the existing Vector collector.
They include method, host, path, status, latency, request ID, gateway and upstream
address. The collector preserves those fields and derives severity from HTTP
status when no explicit severity is present. The Application overview dashboard
matches upstream pod addresses to show access logs for the selected apps.

Envoy 1.36 uses `%REQ(...)%` for request headers. Validate formatters against the
bundled Envoy version, since a rejected log formatter can prevent a listener from
starting even while Gateway status reports Programmed.

## Bootstrap and recovery

`task flux:bootstrap` installs the committed Gateway CRDs, waits for establishment
and restarts the Cilium operator before connecting Flux. Cilium discovers these
CRDs at operator startup. Flux then manages the CRDs and Gateway resources. The
certificate can be reissued using the independently recoverable DNS credentials.
The proxy holds no application data.

The CRD set includes TLSRoute because Cilium 1.19's backend watcher lists it even
for HTTP-only gateways. Without it, [backend Service updates can fail to reconcile](https://github.com/cilium/cilium/issues/47819).

After recovery or a controller upgrade, verify both address families: TLS and
HTTP redirects, application health and login pages, OIDC discovery, access logs,
Minecraft, and fresh management connections. Check route Accepted/ResolvedRefs
conditions and Envoy listener acknowledgement as well as Gateway Programmed.
Prove unrelated pods cannot reach backend ports directly and unpublished VIP
ports are closed. For UDP, inspect Cilium drop verdicts rather than relying on a
timeout alone.

Revert faulty routing changes through Git. Keep the host firewall enforcing during
recovery and use the [saved-policy rollback procedure](../../../../docs/rke2-operations.md#host-firewall-handoff)
for a firewall change. Do not delete the host policy to restore connectivity.

## References

- [Cilium 1.19.6 Gateway API support](https://github.com/cilium/cilium/blob/v1.19.6/Documentation/network/servicemesh/gateway-api/gateway-api.rst)
- [Gateway class service and access-log API](https://github.com/cilium/cilium/blob/v1.19.6/pkg/k8s/apis/cilium.io/v2alpha1/gatewayclassconfig_types.go)
- [Cilium ingress policy and source IP behavior](https://github.com/cilium/cilium/blob/v1.19.6/Documentation/network/servicemesh/ingress-reference.rst)
