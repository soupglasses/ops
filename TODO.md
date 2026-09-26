<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# TODO

Findings from the July 2026 architecture review, ranked. Data-loss items first.

## Backups / disaster recovery

- [x] **Refresh local access after the RKE2 rebuild.** `task kube:kubeconfig` fetches
  and verifies the current credentials before replacing the operator's local file.
  On 2026-09-26, verified upstream DNS and authenticated TLS access through
  `k8s.home.finnes.dev`; the RKE2 serving certificate includes this name.
- [ ] **Retire remaining router-local home.arpa names.** The API now uses upstream
  DNS. Inventory replacement records before removing DHCP-generated names used by
  other LAN devices, and migrate verified SSH host-key aliases independently.

- [x] **Recover OpenLDAP from off-cluster backups.** On 2026-09-26, restored the
  September 9 and September 8 restic snapshots into separate ZFS PVCs. Both contained
  the database and an 88-entry portable LDIF dump; the running LDAP server contains six
  person entries. Legacy datasets remain untouched. Fresh backup and login acceptance
  checks remain part of the migration below.
- [x] **Complete application backup restore drills.** Fresh LDAP, Minecraft, metrics
  and logs backups restored into separate PVCs; native Victoria queries verified
  historical samples and both services' own logs. Evidence and remaining acceptance
  limits are in [the recovery record](docs/rke2-recovery-verification.md).
- [ ] **Complete recovery safety checks.** VolSync 0.15 can initialize a missing
  repository and publish an empty successful restore. LDAP/Minecraft now require their
  data files before startup and withhold backup triggers on preparation failure.
  The new hooks and fresh S3 restores passed live checks. Review the existing manual
  PVC-replacement helper's interaction with Helm/operator reconciliation before using
  it for in-place monitoring recovery. VictoriaLogs initialization was explicitly
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

- [x] **Per-app observability dashboard.** Grafana's Application overview selects
  namespace and multiple apps, with usage gauges, peak statistics, resource requests, logs, alerts,
  and PVC-linked VolSync backup status. Scoped kube-state-metrics and kubelet
  scrapes are live. [Usage guide](docs/application-dashboard.md).

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
- [x] **Prove Alertmanager notifications off-cluster.** Email delivery through the
  configured SMTP receiver was confirmed by the operator on 2026-09-26.
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

- [x] **Restore Minecraft public access after RKE2 migration.** Shared VIP Services
  now use compatible `Cluster` policy, Minecraft's chart receives its dual-stack
  settings, and a port-specific ingress rule permits game traffic through RKE2's
  default namespace isolation. Minecraft status handshakes passed via the VIP,
  public IPv4 and public IPv6 on 2026-09-26. HTTPS remained available on both families.

- [ ] **Reassess shared VIP policy with Cilium 1.21.** Minecraft and public ingress
  use `externalTrafficPolicy: Cluster` because Cilium 1.19 rejects shared `Local`
  Services selecting different pods. [Upstream change #45819](https://github.com/cilium/cilium/pull/45819)
  adds `lbipam.cilium.io/sharing-permit-different-pods`. Verify availability in the
  RKE2-supported release and BGP behavior before adopting it: the annotation relaxes
  allocation checks but does not ensure every advertising node has both backends.
  Consider dedicated IPv6 Service addresses with ExternalDNS and `Local` policy
  instead. Validate Minecraft and HTTPS over both families after any change.

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

## Log ingestion follow-up

- [x] Provision a Kubernetes-managed node overview with stacked per-app CPU and memory,
  host totals, disk utilization, ZFS state and network traffic using existing scrapes.
- [x] Label warning-prefixed messages conservatively while preserving their text.
- [x] Add inspected, guarded ZFS bootstrap-clone maintenance tasks. Refuse active
  recovery images and data-bearing origins.
- [ ] Complete Cilium Gateway production cutover after dual-stack staging and backend
  isolation checks. Keep native Cilium Services for Minecraft TCP/UDP.
- [ ] Verify the RKE2 1.35.8 upgrade live, including fresh dual-stack management access,
  BGP, application data and off-cluster recovery points.
- [x] Count compressed RKE2 S3 snapshots correctly. The collector now reports 24
  scheduled snapshots and the stale-backup alert has cleared.
- [x] Normalize severity and parse structured application fields at ingestion, including
  klog, logfmt, Minecraft and OpenLDAP connection statistics. Test representative lines
  with Vector before rollout.
- [x] Resolve OpenEBS cleanup of retired Victoria restore sources through independently
  reviewed clone promotion. Historical queries and fresh ingestion passed, volume
  identity and quotas were preserved, and ordinary controller cleanup completed.
- [ ] Reduce idle CPU in the Minecraft and LDAP quiesce watchers. Their five-second
  kubectl polling currently consumes about 3% of one core each between backups.

- [ ] Show healthy configured alert rules scoped to the selected namespace and apps.
  Grafana’s native alert list filters labels on active instances, so inactive generic
  rules cannot currently be scoped this way. Keep the app view scoped and avoid
  duplicating alert definitions or changing alert evaluation for the dashboard.

- [x] Replace OpenEBS host-path Endpoints leader election with the upstream Lease-based
  provisioner. Disable incompatible XFS quotas on the Ubuntu ext4 root filesystem.
  Existing application data uses quota-enforced ZFS PVCs.
