<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# TODO

Findings from the July 2026 architecture review, ranked. Data-loss items first.

## Backups / disaster recovery

- [x] **Recover OpenLDAP from off-cluster backups.** On 2026-09-26, restored the
  September 9 and September 8 restic snapshots into separate ZFS PVCs. Both contained
  the database and an 88-entry portable LDIF dump; the running LDAP server contains six
  person entries. Legacy datasets remain untouched. Fresh backup and login acceptance
  checks remain part of the migration below.
- [ ] **Complete recovery safety checks.** VolSync 0.15 can initialize a missing
  repository and publish an empty successful restore. LDAP/Minecraft now require their
  data files before startup and withhold backup triggers on preparation failure.
  Validate the new hooks live and prove VictoriaLogs' first S3 backup restores queryable
  records before enabling scheduled backups. VictoriaLogs initialization was explicitly
  approved on 2026-09-26; its repository had no snapshots.
- [ ] **Single age key is a single point of total backup loss.**
  `cluster-secrets.sops.yaml` (holding `SECRET_PASSWORD`, the restic repo password) is
  encrypted to one recipient (`.sops.yaml`). Losing `.secure/age.agekey` makes the
  Hetzner ciphertext permanently unreadable. Add a second cold-storage age recipient
  and re-encrypt; keep an offline copy of the restic password itself.
- [ ] **Backups are deletable by the credentials that write them.** One bucket, one
  provider, and the in-cluster S3 keys can `restic forget` everything. Retention is
  ~4 weeks (daily 7 / weekly 4, prune 14d in
  `kubernetes/components/volsync/replicationsource.yaml`); corruption unnoticed for
  longer is unrecoverable. Enable bucket versioning or object lock, add a monthly
  retention tier, and run a periodic `restic check` CronJob.
- [x] **Restore destroys the only local copy before verifying.** The restore helper now
  takes and waits for a local VolumeSnapshot before deleting the PVC, and retains it
  until application-level validation succeeds.
- [x] **volsync cache PVCs inherit the data size.** `cacheCapacity` defaults to
  `${VOLSYNC_CAPACITY}`, so minecraft allocates a 50Gi restic cache that needs ~5Gi.
  Introduce a separate `VOLSYNC_CACHE_CAPACITY` variable.
- [x] **Stale minecraft backup alert.** `minecraft/app/prometheusrule.yaml` alerts on
  a `container="mc-backup"` sidecar that no longer exists, so it can never fire. The
  real coverage is the volsync rules in `volsync-system`. Delete the stale rule.
- [x] **minecraft ks.yaml has no dependsOn.** On a from-zero rebuild it races
  openebs/volsync and retry-loops until they converge, and the restore-once
  ReplicationDestination can fire under half-ready conditions. Add dependsOn openebs +
  volsync (openldap now has this pattern).
- [x] **Orphaned settings.** `S3_ENDPOINT`/`S3_BUCKET` in
  `components/common/config/cluster-settings.yaml` are consumed by nothing (the live
  value is the encrypted `SECRET_S3_ENDPOINT`); they can drift silently. Remove or use.

## Platform

- [ ] **Finish the simplified RKE2 recovery.** Ubuntu/RKE2 and ZFS verified live on
  2026-09-26; the unchanged Flux controllers are installed and both bootstrap Secrets
  have been imported by the operator. Router management is moving to native RouterOS;
  application recovery and host-policy adoption remain gated. Validate recovered LDAP,
  Minecraft and logs, off-cluster restore drills, both BGP families, notifications and
  reboot before closing. Keep the old datasets and defer RAIDZ2 reformatting.
- [ ] **Retire the RouterOS automation identity after handoff.** Verify the independent
  router administrator path and off-device encrypted backup first. Preserve working
  BGP/firewall configuration and the controller DHCP reservation.
- [ ] **Audit mode did not survive the Cilium restart.** Live `PolicyAuditMode` was
  `Disabled` on 2026-09-26 despite the old inventory default. Verify fresh dual-stack
  access and recovery login before Flux adopts the existing host policy; use console
  rollback rather than relying on a persistent audit flag.

- [x] **Evaluate RKE2 backup alerts in-cluster.** Deploy a tightly selected VictoriaMetrics
  stack so the host ServiceMonitor is scraped and its PrometheusRule is evaluated and
  delivered to VMAlertmanager after a clean rebuild. Metrics and logs now target one
  year of HDD ZFS storage with encrypted S3 recovery; complete the migration and restore
  evidence before considering their persistence verified.
- [ ] **Prove Alertmanager notifications off-cluster.** The evaluator, dedicated SMTP
  receiver, and encrypted `alertmanager-smtp` Secret are declared. Trigger and receive a
  test firing/resolved notification after bootstrap.
- [ ] **Complete the Talos-to-RKE2 recovery.** Ansible now describes Ubuntu 26.04,
  systemd-networkd, the CIS-oriented host baseline, RKE2, bundled Cilium, ZFS, and
  RouterOS. Converge it, bootstrap Flux, complete both S3 restore passes from
  `docs/zfs-s3-recovery.md`, then remove the preserved Talos configuration.
- [ ] **Finish the 2026-09-21 RKE2 recovery checkpoint.** The host is converged and
  verified at `172.21.71.10`, the old ZFS datasets remain unmounted, and RouterOS has
  the authenticated dual-stack BGP peers plus narrowly scoped firewall rules. Next:
  bootstrap Flux using the human-owned `.secure` credentials; reconcile all
  Kustomizations; verify both BGP sessions and `/32` plus `/128` VIP routes; prove
  dual-stack pod, Service, DNS, ingress, and egress traffic; then run the two-pass S3
  application restore drill. Do not enable host-firewall enforcement or remove Talos
  data/configuration before those checks pass.
- [x] **Prove an independent human recovery login.** The operator confirmed
  `ssh sofie@nas` and physical console access on 2026-09-26. Independent RouterOS
  administrator SSH also passed; its off-device encrypted backup is still pending.
- [ ] **Complete the new observability acceptance tests.** After Flux bootstrap, verify
  VictoriaMetrics Operator/CRDs, VMAgent, VMAlert, VMAlertmanager, Grafana, VictoriaLogs,
  Vector journald/Kubernetes ingestion, and the host exporters. Prove a firing and
  resolved SMTP message, Grafana admin login, useful LogSQL results, stale-backup
  alerting, and a VictoriaLogs backup restore from an older recovery point.
- [ ] **Add VictoriaLogs security and platform logging.** Collect SSH/sudo authentication,
  auditd, AIDE, kernel/AppArmor, RKE2/containerd, Cilium/Hubble policy verdicts,
  RouterOS firewall/BGP, VolSync/restore, and Flux/controller reconciliation events.
  Keep health/rate alerts in Prometheus and event detail in VictoriaLogs/Grafana. Include
  the sanitized `rke2-etcd-snapshot-metrics.service` failure event, but never ingest its
  transient raw snapshot listing, S3 object names, endpoint, or credentials.

- [x] **Pin day-zero Cilium and align native routing.** RKE2 and its bundled Cilium are
  pinned in Ansible; the cluster and Cilium both use `10.42.0.0/16` plus the documented
  IPv6 pod CIDR. Flux owns the post-bootstrap Cilium policy resources.
- [ ] **Keep RKE2 upgrades deliberate.** RKE2 is pinned to `v1.34.11+rke2r1`.
  Confirm fresh S3/etcd backups and the recovery token, then upgrade one Kubernetes
  minor at a time with the Ansible upgrade playbook and full post-upgrade verification.
- [x] **Use static node addresses.** Ansible configures native systemd-networkd with
  `172.21.71.10/24` and `2a13:e745:1ee8:71::10/64`; the DHCP reservation remains only
  as collision protection.
- [ ] **PodSecurity: workloads violate `restricted`.** canaille lacked
  allowPrivilegeEscalation=false, drop ALL, runAsNonRoot, seccompProfile; likely
  repo-wide. Sweep all Deployments and chart values.
- [x] **Canaille did not trust ingress proxy headers.** OIDC metadata used HTTP URLs
  behind TLS-terminating ingress-nginx, causing Authlib validation failures and 500s
  after client creation. Enable legacy forwarded-header handling for the single proxy hop.
- [ ] **Canaille token administration has two upstream UI bugs.** Version 0.3.3 still
  crashes after revoking client-credentials tokens because audit logging assumes a user
  subject, and its Copy buttons submit the surrounding form. Report and track upstream;
  do not carry local source patches.

## Flux / reconciliation

- [x] **Failure recovery and drift wait up to 1h.** Every app Kustomization and
  HelmRelease has `interval: 1h`; a HelmRelease that exhausts its retries sits failed
  until the next tick. Reconciles measure sub-second on this cluster, so drop app
  ks.yaml intervals to 10m and HelmReleases to 30m; consider
  `driftDetection: enabled` on HelmReleases.
- [ ] **`task flux:reconcile` only kicks the root Kustomization.** Fine after a push
  (revision changes propagate), but it does not retry failed apps; `flux:reconcile-ks`
  does, serially and slowly. Consider a parallel variant or an alias that also
  reconciles cluster-apps.
- [ ] **Push detection is a 1m git poll.** A GitHub webhook into a Flux Receiver
  would make it instant, at the cost of exposing notification-controller publicly.
  Optional polish.
- [x] **cilium-bgp ks.yaml declares `namespace: kube-system`** but the parent
  kustomization rewrites it into `network`, where it actually lands. The declared
  namespace is misleading; align it.

## Networking / future

- [ ] **Verify RKE2 and Cilium dual stack live.** Day-zero config now has IPv6 pod and
  service CIDRs, dual-stack IPAM, and dual-stack public Services. Prove pod, service,
  DNS, and egress connectivity before considering this complete.
- [ ] **Complete and verify native IPv6 application exposure.** RouterOS uses the ISP's static
  `2a13:e745:1ee8::/48`; trusted LAN and server VLAN SLAAC are live on subnet IDs 69
  and 71. Reserve subnet 68 for Kubernetes service VIPs and 70 for WireGuard. After
  dual stack is live, advertise IPv6 service `/128`s over BGP, then add explicit WAN
  firewall accepts and AAAA records per public service.
- [x] **Move Kubernetes to an isolated server VLAN and native WAN ingress.** `ether8`
  is now the VLAN 71 access port, the node is 172.21.71.10, Cilium advertises the
  shared 172.21.68.10 VIP to RouterOS, and 80.248.139.51 is DNATed to that VIP. New
  server-to-trusted-LAN connections are blocked while replies and internet egress work.
- [ ] **Gateway API migration (before palworld/nextcloud/immich).** Replace
  ingress-nginx and the lbipam sharing-key hack with one Gateway owning the single BGP
  IP 172.21.68.10. Recommended implementation: Envoy Gateway (Flux-managed, supports
  TCPRoute/UDPRoute/TLSRoute; Cilium's implementation has no UDPRoute). Steps:
  experimental-channel Gateway API CRDs as their own ks, Envoy Gateway in `network/`,
  one Gateway with HTTPS/TCP/UDP listeners, convert the single canaille Ingress to an
  HTTPRoute, point external-dns at gateway-httproute sources, let cert-manager issue
  via the Gateway, retire ingress-nginx-public. Keep ingress-nginx-tailscale until a
  Tailscale-class Gateway replaces it. Game servers may stay direct LoadBalancer
  Services on the shared IP if the Envoy hop shows in latency.
- [ ] **Per-app LDAP service accounts with ordering.** Built and live-tested:
  `components/ldap-account` + `tofu/ldap/service-account` (design in
  `docs/ldap-service-accounts.md`). tofu-controller 0.16.4 is deployed and
  `SECRET_LDAP_ADMIN_PASSWORD` is in cluster-secrets. Remaining: the gRPC-hang
  follow-ups in `docs/tofu-controller-grpc-hang.md` if the hang recurs.
- [ ] **ldap-account should bind as a delegated provisioner, not the rootdn.** The
  component currently stamps the `cn=admin` password into each consuming namespace;
  a namespace-level compromise there would yield the whole identity system. Create
  `uid=provisioner,ou=system` in `tofu/ldap/base` with a slapd ACL restricted to
  managing `ou=system`, switch the component to `SECRET_LDAP_PROVISIONER_PASSWORD`,
  and retire the admin password from cluster-secrets.
- [ ] **Every namespace carries cluster-secrets and the sops age key** (common
  component), because per-namespace Kustomizations decrypt and substitute locally.
  Accepted homelab tradeoff for now, but worth revisiting: the age key in any
  namespace decrypts every secret in the repo. Keep new cluster-wide secrets to the
  minimum and prefer per-app scoped credentials.
- [ ] **Cilium egress gateway idea** parked in `docs/cilium-egress-gateway.md`.
- [x] **README still says BGP is "planned"**; it is implemented. Refresh the intro.

## Renovate / PR hygiene

- [ ] **Rotate GitHub automation tokens annually.** The `soupbot` Flux source token
  (`public_repo`) and commit-status token (`repo:status`) have no provider expiry.
  Rotate both by 2027-08-27, keeping the credentials separate and least-privileged.

- [ ] Renovate automerges only GitHub Actions and mise tools, so chart/container PRs
  pile up. Automerge patch-level helm/docker updates gated on the flux-local CI check.
- [ ] Majors needing human review: prometheus-operator-crds 30 (#76, CRD changes),
  actions/checkout v7 (#75, runner node version), error-pages 4 (#66, default
  backend), terraform ldap 0.13 (#74, provider under the data_json workaround),
  helm 4 (#23, conflicted, breaking CLI changes).

## Tooling

- [ ] Guard the completions in `.mise.toml` with a tool-exists check so a fresh
  `mise trust` does not spew errors for tools not yet installed.
- [ ] Find a first-party Flux way to visualize the Kustomization dependsOn order
  (topo-sorted tree) as a `task`. Prefer `flux tree` or similar over hand-rolled
  parsing.
