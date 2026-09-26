<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# Public gateway

Cilium owns public HTTP/TLS routing and native TCP/UDP service load balancing.
Gateway API v1.4.1 matches Cilium 1.19.6. That controller supports HTTPRoute,
GRPCRoute and experimental TLSRoute, but not TCPRoute or UDPRoute. Minecraft
therefore uses its existing Cilium LoadBalancer Service.

| Endpoint | Backend | Exposure |
| --- | --- | --- |
| `auth.finnes.dev` HTTPS | `identity/canaille:80` | Public Gateway |
| `grafana.finnes.dev` HTTPS | `monitoring/victoria-metrics-grafana:80` | Public Gateway |
| `mc.finnes.dev:25565` TCP | `default/minecraft:25565` | Native Cilium LoadBalancer |
| `mc.finnes.dev:24454` UDP | Minecraft voice chat | Same LoadBalancer |
| `openldap.identity.svc:636` TLS | OpenLDAP | ClusterIP, private |
| `minecraft-rcon.default.svc:25575` TCP | Minecraft RCON | ClusterIP, private |

The shared VIP is `172.21.68.10` and `2a13:e745:1ee8:68::10`. Public IPv4 DNS
points at the router's WAN address, which forwards traffic to the VIP. Public IPv6
DNS points directly at the VIP. All sharing Services use `Cluster` traffic policy.
Cilium's HTTP proxy preserves the client address in forwarded headers with zero
trusted proxy hops. Minecraft retains the current native Service behavior.

The gateway obtains its own DNS01 certificate through the existing ClusterIssuer.
Existing Ingress certificates remain available for rollback. Routes preserve the
original hostnames and paths, redirect HTTP to HTTPS, set HSTS and remove server
identification headers. Authentication stays in the applications. Nginx-specific
custom error pages, buffering settings and compression knobs are not required by
these routes.

Native Envoy JSON access logs go to container stdout and the existing Vector
collector. They include HTTP status, method, host, request ID, latency, gateway and
upstream address. The gateway namespace identifies the proxy, not its backend.
No additional logging service is needed.

## Staging and cutover

The Gateway Kustomization starts suspended while its CRDs are installed. It initially listens on 8080 and
8443 so it can share the VIP without competing with nginx on 80 and 443.

1. Reconcile `network/cilium-gateway-api-crds`. Wait for its five standard CRDs to be
   established. They are pinned to the Gateway API v1.4.1 source commit and are not
   pruned with the application.
2. Enable `gatewayAPI.enabled: true` in the RKE2-managed Cilium HelmChartConfig,
   keeping `gatewayAPI.externalTrafficPolicy: Cluster`, `xffNumTrustedHops: 0`
   and host networking disabled. Retain all existing Cilium settings. Verify
   `CiliumGatewayClassConfig` is installed, then wait for healthy Cilium and both
   BGP peers before continuing.
3. Resume `network/cilium-gateway`. Check the Certificate is Ready, Gateway is
   Programmed, and each HTTPRoute reports Accepted and ResolvedRefs. Confirm its
   generated Service has both VIP addresses, no NodePorts, and ports 8080/8443.
4. Test both address families using `curl --connect-to` to send the normal HTTPS
   host and TLS SNI to staging port 8443. Check Canaille discovery/login, Grafana
   `/api/health` and login redirects, WebSocket upgrades and emitted access logs.
   HTTP on 8080 must redirect to 8443. Unknown hostnames must have no backend.
5. Add `gateway-httproute` to ExternalDNS sources only after the CRDs exist.
   The Gateway target annotation supplies `${IP_ADDRESS},${IPV6_ADDRESS}` to
   all its routes, including the HTTP redirect. Preserve the TXT owner and prefix.
   Confirm planned DNS records match before removing either Ingress source.
6. Move nginx's HTTP/TLS Service ports away from 80/443, then change Gateway
   listeners to 80/443 and the redirect port to 443. Keep nginx running with its
   Ingress resources until production checks pass. The listeners cannot coexist
   on the same VIP and ports, so this requires a brief controlled cutover.
7. Repeat application, DNS, TLS, firewall and Minecraft checks. Confirm normal
   browser login and Grafana Live behavior. Remove nginx only after successful
   production verification. Its Tailscale instance currently has no routes and
   can be retired with the unused public controller. Keep the Tailscale operator.
8. Remove the temporary nginx source allowances from the frontend policies and
   retain only the gateway, host probes and monitoring access.

The ingress proxy has an explicit backend allowlist. The two frontend policies
admit the existing public nginx controller during staging and the Cilium ingress
identity. Direct connections from unrelated pods are denied. Egress from the
applications is unchanged, including LDAP and identity-provider connections.

## Access and rollback checks

Do not change `rke2-host-ingress` as part of this migration. Its current management
source networks remain allowed to SSH/API on `172.21.71.10` and
`2a13:e745:1ee8:71::10`. Test fresh SSH and API connections over both families
before enabling the controller, after its rollout, and after the traffic cutover.
Existing SSH sessions alone are not sufficient evidence. `task
ansible:firewall:verify` checks these paths and BGP state.

From an independent client, test the published ports and prove unrelated VIP ports
are closed, including RCON 25575, LDAP 636, Kubernetes API 6443 and arbitrary TCP/UDP
ports. Check UDP verdicts with Hubble or packet capture because a UDP timeout alone
does not prove a drop. Also prove unrelated pods cannot connect directly to the
frontend HTTP ports. Keep physical console access available.

Before cutover, rollback means suspending the gateway Kustomization and removing
its staged Gateway. Production nginx and Minecraft are unchanged. After cutover,
first move the Gateway back to 8080/8443, then restore nginx Service ports 80/443.
Restore DNS source annotations before deleting routes when ExternalDNS uses sync
policy. Revert the application ingress policies only if they prevent the old
serving path, leaving the existing host policy in place.

No host-policy change is needed here. A separate firewall experiment must capture
the previous policy and arm a timed local reapply before changing it, then cancel
the timer only after fresh dual-stack management checks. Deleting the host policy
is not a fail-closed rollback.

## References

- [Cilium 1.19.6 Gateway API support](https://github.com/cilium/cilium/blob/v1.19.6/Documentation/network/servicemesh/gateway-api/gateway-api.rst)
- [Cilium 1.19.6 Gateway class service and access-log API](https://github.com/cilium/cilium/blob/v1.19.6/pkg/k8s/apis/cilium.io/v2alpha1/gatewayclassconfig_types.go)
- [Cilium ingress policy and source IP behavior](https://github.com/cilium/cilium/blob/v1.19.6/Documentation/network/servicemesh/ingress-reference.rst)
