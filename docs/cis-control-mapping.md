<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# CIS hardening and verification

The host uses a CIS-derived Ubuntu baseline and RKE2's `cis` profile. This is a control
map and evidence checklist, not a claim of Canonical certification for Ubuntu 26.04.

| Control area | Enforcement | Verification |
| --- | --- | --- |
| Patch management | unattended security upgrades; no surprise reboot | inspect unattended-upgrades logs |
| Host access | root password locked; SSH keys only; no root, password, X11, agent, or TCP forwarding | `sshd -T`; automation login test |
| Password policy | pwquality and conservative login defaults | inspect effective PAM/password policy |
| Mandatory access | AppArmor installed and enabled | `aa-status` |
| File integrity | initialized AIDE database and daily persistent timer | `aide --check`; timer status |
| Audit | auditd, retained logs, failure actions, config/sudo/time/delete/module watches | `auditctl -l`; controlled event search |
| Kernel/network | hardened sysctls with explicit Kubernetes/Cilium exceptions | `sysctl --system`; compare managed file |
| Host firewall | Cilium host firewall; Flux-owned node policy for management, cluster, BGP, and ICMP traffic | `cilium-dbg status`; Hubble verdicts; external port scan |
| Kubernetes | RKE2 `profile: cis`, secrets encryption, protected kernel defaults | release-matched RKE2 CIS 1.12 scan |

Forwarding, bridge netfilter, eBPF, user namespaces, overlayfs, and ZFS are required
platform exceptions. RouterOS remains the perimeter firewall; Cilium is the only host
packet-policy engine. RKE2 management ports are limited to the trusted LAN and
WireGuard prefixes. SSH remains available from internal IPv4 and the owned IPv6 `/48`
to make firewall enforcement recoverable without exposing it to the public Internet.

The first convergence must set `host_firewall_audit_mode=true`. Exercise SSH, the API,
monitoring, BGP, backup, and public traffic while watching host-endpoint policy verdicts.
Then rerun with the committed default (`false`) and require the verification playbook
to pass. Audit mode is temporary and resets when the Cilium agent restarts.

Before production recovery is declared complete, archive the RKE2 CIS scan result and
the effective host checks with the restore evidence. Re-run them after every RKE2,
kernel, or Ubuntu upgrade. Any failed control needs an explicit exception, rationale,
owner, and review date.
