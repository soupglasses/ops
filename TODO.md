<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: MIT
-->

# TODO

Findings from the July 2026 architecture review, ranked. Data-loss items first.

## Application reconciliation

- [ ] Roll out the Jellyfin Upjet replacement after publishing and pinning the
  provider image,
  and satisfying security/resilience review. Reuse the existing `ops-recovery`
  administrator. Verify observe-only adoption before
  handing writes over from the sidecar. Local generated-connector tests and Flux
  validation pass; live adoption and recovery evidence are still required.
- [ ] Hand Jellyfin LDAP plugin installation over to the provider after removing
  image seeding and proving repository, update-schedule and restart ordering.
  The runtime currently overwrites plugin binaries on every start; adding a
  competing managed Plugin before removing that behavior would be unsafe.

- [x] Add structured Jellyfin reconciliation diagnostics: successful write counts,
  API resources, changed field names, library operations and request failure context.
  Keep setting values and credentials out of logs; preserve quiet idle polling.
  Fourteen HTTP tests cover summaries, failures, partial progress and authentication.

- [x] Stop logging idle Jellyfin configuration polls as applied changes. The success
  message was unconditional; log only successful configuration writes while keeping
  health checks fresh. HTTP regression tests cover idle polls and drift repair.
- [x] Deploy the updated Jellyfin configuration sidecar and verify two minutes of
  quiet polls, both containers Ready, zero restarts and Flux Ready/Healthy at
  `9ef4e71`. Runtime CI, LDAP integration and 57 Flux validation checks passed.

- [x] Scope LDAP platform configuration independently of Jellyfin, adopt app-local
  `GroupOfNames` declarations through the shared administrative provider, mark
  generated files in Git, expose generation/export tasks, and split runtime CI
  by subsystem. Real composition tests and live group identity preservation passed.

- [x] Parse Jellyfin console logs into searchable message, logger, thread and time
  fields, with normalized Grafana severity; preserve unrecognized lines.

- [x] Upgrade Jellyfin to 12.2 with LDAP 24 and verify `jellyfin.user` /
  `jellyfin.admin` roles; provision the LDAP admin, preserve existing access and
  remove the retired group. Restore and live authorization evidence is in
  [the recovery record](docs/application-reconciliation.md#jellyfin-122-upgrade-recovery).

- [x] Replace OpenTofu with the existing Crossplane Upjet provider, remove the
  controller/modules/tooling, and enable Grafana OIDC with `OAuth2Client`.
  [Migration and recovery evidence](docs/opentofu-to-crossplane-migration.md)
  records identity-preservation tests and the real off-cluster restore drill.

- [x] Deploy the pinned Grafana OIDC-picture build from `soupglasses/grafana`.
  Source release and anonymous image pulls verified; live Grafana reports
  `13.2.4-oidc.1` with a healthy database. Synthetic sign-in and CI cover
  profile/navigation pictures, updates, removal and existing access controls.
- [ ] Verify a real Canaille photo in Grafana after signing out and back in.
  Gravatar remains disabled.

- [x] Keep Jellyfin LDAP password changes disabled; omit the redundant account-menu
  link and unusable external reset URL. Manage passwords directly in Canaille.

- [x] Accept Jellyfin LDAP username and email logins with one canonical account;
  verify email-first login, username relogin and upstream photo synchronization.

- [x] Simplify Jellyfin to the unpatched upstream LDAP plugin and fold server ACLs
  into the existing stock-image OpenLDAP deployment; remove the separate ACL package.

- [x] Publish MIT-authored LDAP SDK reconciliation, OAuth2Client composition and
  pinned Jellyfin integration images. Real kind/Helm, LDAP, photo synchronization,
  password login, configuration drift and recovery tests pass in CI.
- [x] Create the isolated compressed ZFS media dataset with a 1 TiB quota and private
  NFSv4 export. Preserve existing datasets; retain the stable media directory on PVC recreation.
- [x] Restore the initial off-cluster media backup into isolated storage and verify
  its canary and manifest; evidence is recorded in `docs/media-storage.md`.
- [x] Restore the initial Jellyfin configuration checkpoint through a separate VolSync
  destination and validate SQLite contents, account identity, libraries, plugins and API settings.
- [x] Verify a real older OpenLDAP backup initializes the current ACL policy while
  preserving the directory database and native export byte-for-byte.
- [x] Remove Cilium L2 announcements; advertise the private Gateway through the existing
  BGP service range, with an exact router prefix and private-only forwarding rules.
- [x] Complete live Jellyfin identity, private BGP TLS and off-cluster configuration
  and media restore acceptance; remove first-install flags after those drills.
- [ ] Repeat the media restore drill with representative files after importing the
  real library; the initial empty-library canary does not prove those future files.

## Copyright attribution

- [x] **Attribute generated files to Sofie Finnes.** Replaced 69 Soup Bot SPDX
  notices across 66 files, including embedded Ansible-generated file headers. No
  OpenAI or GPT copyright notices were found in tracked non-secret files.
- [ ] **Update the remaining encrypted-file copyright header manually.**
  `ansible/inventories/home/group_vars/all/secrets.sops.yaml` still credits Soup Bot.
  All SOPS files were left unchanged so the operator can preserve their integrity
  metadata when updating the header.
- [ ] **Confirm upstream copyright exceptions.** Preserved onedr0p in
  `.renovaterc.json5`, Yaal Coop in
  `kubernetes/apps/identity/openldap/app/oauth.schema.conf`, and Ales Lerch in
  `kubernetes/apps/volsync-system/volsync/app/prometheusrule.yaml`; these appear to
  credit upstream work and need provenance review before changing attribution.

## Backups / disaster recovery

- [ ] **Refresh shared VolSync restore destinations.** The shared component still
  initializes destinations with `manual: restore-once`; it does not advance their
  recovery snapshots with subsequent backups. Jellyfin already declares a daily
  refresh. Review the shared contract separately so other PVC rebuilds do not use
  a stale initial snapshot; OpenLDAP was explicitly refreshed after this migration.

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
- [x] **Keep RKE2 upgrades deliberate.** The live node runs `v1.35.8+rke2r1`;
  Ansible now prepares the next supported minor, `v1.36.4+rke2r1`.
  Renovate can propose coupled release/checksum updates without dashboard approval.
  Maintenance requires recovery and release review, rejects interrupted upgrades and
  unsupported Cilium transitions, and checks the bundled network rollout. See
  [RKE2 operations](docs/rke2-operations.md) for Cilium preflight and recovery.
- [x] **Preserve Cilium ingress ownership across RKE2 upgrades.** The host role and
  upgrade playbook install an explicit `ingress-controller: [none]` drop-in.
  RKE2 1.36 otherwise chooses Traefik when no ingress-nginx chart exists. Syntax,
  lint, manifest validation and independent review passed before maintenance.
- [ ] **Repair the documented automation SSH login.** The `ansible` account rejected
  its configured key on 2026-09-27. The hardware-backed `sofie` login and sudo work;
  do not broaden source restrictions or replace credentials without diagnosing it.
- [ ] **Apply the prepared RKE2 1.36 maintenance stage.** Complete the documented
  recovery preconditions and upgrade verification before advancing the desired
  version again. [PR #53](https://github.com/soupglasses/ops/pull/53) remains blocked
  on this ordering and Gateway API compatibility with Cilium 1.19.6.
  Reconfirmed on 2026-10-12: the node still runs 1.35.8, and the task workspace
  has read-only cluster access without the SSH identity/host trust required to run
  maintenance. Keep #53 open until that stage and network preflight are verified.
- [x] **Review and deploy the dependency backlog (2026-09-27).** Merged 24 reviewed
  PRs, including the new Alpine guard update; closed two obsolete Talos updates.
  Fixed DNS annotation rollout, distroless log-server guard, Vector node lookup,
  and the editorconfig executable rename. Final manifest validation passed;
  controllers, recent log ingestion and metric targets were healthy. LDAP's
  provider upgrade produced a no-change plan. OpenEBS snapshot/clone and VolSync
  RS/RD/PVC population restored verified synthetic contents; cross-version restic
  older/latest recovery passed. Evidence is in [PR #9](https://github.com/soupglasses/ops/pull/9)
  and [PR #46](https://github.com/soupglasses/ops/pull/46). Temporary resources were removed.
- [x] **Use static node addresses.** Ansible configures native systemd-networkd with
  `172.21.71.10/24` and `2a13:e745:1ee8:71::10/64`; the DHCP reservation remains only
  as collision protection.
- [ ] **PodSecurity: workloads violate `restricted`.** canaille lacked
  allowPrivilegeEscalation=false, drop ALL, runAsNonRoot, seccompProfile; likely
  repo-wide. Sweep all Deployments and chart values.
- [x] **Canaille did not trust ingress proxy headers.** OIDC metadata used HTTP URLs
  behind TLS-terminating ingress-nginx, causing Authlib validation failures and 500s
  after client creation. Enable legacy forwarded-header handling for the single proxy hop.
- [x] **Publish Canaille's profile editability fix.** The fork image is upstream 0.3.6
  plus the form settings fix; all 60 affected tests passed inside the published image.
  [Release pipeline](https://gitlab.com/soupglasses/canaille/-/pipelines/2959094298).
  To rebuild after registry loss, clone `git@gitlab.com:soupglasses/canaille.git`,
  check out `0.3.6-soupglasses.3`, and run `docker build -t canaille-recovery .`.
  Push the rebuilt image to an available registry and update the deployment digest.
  The release tag's `.gitlab-ci.yml` documents the image tests and publishing process.
- [ ] **Return Canaille to upstream images after the profile form fix is released.**
  Track [upstream MR !351](https://gitlab.com/yaal/canaille/-/merge_requests/351).
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
- [x] **Gateway API migration.** Cilium serves public HTTP/TLS through HTTPRoutes,
  with native LoadBalancer Services for Minecraft TCP/UDP on the shared dual-stack
  VIP. Both nginx controllers and all Ingress resources are removed. ExternalDNS
  follows Services and HTTPRoutes. Public application checks passed over IPv4 and
  IPv6, with backend isolation and management access preserved.
- [x] **Cut over OpenLDAP to the tested Nfrastack runtime.** Production uses
  `openldap-directory` with native config/data exports and UID/GID 389 movers.
  Restored and validated a real off-cluster backup before cutover; archived the
  pinned image for recovery. UUIDs and login names were preserved. Canaille and
  Flux are healthy and the first production-volume backup succeeded. See the
  [migration evidence](docs/openldap-nfrastack-migration.md). Removed the one-time
  example system after migration; production and recovery configuration remain.
- [ ] **Retire LDAP rollback volumes after acceptance.** Original `openldap` and
  `openldap-nfrastack-stage` claims remain Flux-managed with pruning disabled.
  Preserve current recovery points before removing these frozen copies.
- [ ] **Reduce remaining LDAP administrative access.** Canaille still binds as
  rootDN. Crossplane now preserves human-managed administrator membership.
  Review the existing backup controller's namespace-wide `pods/exec` permission
  when narrowing backup permissions across applications.
- [ ] **Every namespace carries cluster-secrets and the sops age key** (common
  component), because per-namespace Kustomizations decrypt and substitute locally.
  Accepted homelab tradeoff for now, but worth revisiting: the age key in any
  namespace decrypts every secret in the repo. Keep new cluster-wide secrets to the
  minimum and prefer per-app scoped credentials.
- [ ] **Cilium egress gateway idea** parked in `docs/cilium-egress-gateway.md`.
- [x] **README still says BGP is "planned"**; it is implemented. Refresh the intro.

## Renovate / PR hygiene

- [x] **Review and deploy the dependency backlog (2026-10-12).** Merged PRs
  #54, #56, #58, #62, #63, #64, #67, #68, #71, #72, #74, #75, #77, #80 and #84.
  Independent native Codex reviews approved the VictoriaMetrics RBAC/CRD changes,
  mise-action major update and OpenEBS ZFS driver patch; Hermes routing was unavailable.
  Observed automatic Flux application and active Kustomization/HelmRelease health
  after each controlled merge; the existing suspended host policy remains excluded.
  All ten PVC identities remained unchanged. Vector sink health, DNS provider
  reconciliation, both backup watchers, public Minecraft 1.21.10 status, and Grafana
  13.2.3 database health passed. VictoriaLogs and VictoriaMetrics opened approximately
  2.6 million and 963 million existing rows respectively. Main CI and local Flate
  validation passed; merged branches were confirmed absent from origin. Runtime
  evidence and specialist verdicts are recorded on the affected PRs.
- [x] **Verify the updated local tools.** Python 3.15.0 and pv-mounter 26.41.0
  installed successfully. The first pv-mounter install used an outdated local Krew
  registry; refreshing its public origin/master resolved the version without changing
  the repository pin. The plugin launches through `mise exec -- kubectl pv-mounter`.

- [x] Run every configured pre-commit check through `task check` and a separate
  GitHub workflow on pull requests and pushes to main.
- [x] Align Kubernetes editor schemas with RKE2 updates and check for stale pins.
  Correct the generated Flux formatting exclusion, add its upstream license
  attribution, and validate its CRDs against version-matched Kubernetes OpenAPI.

- [x] Audit migration dependencies with Renovate extraction and update fixtures.
  Couple RKE2 checksums, Gateway CRDs and runtime/restore image pins. Derive the
  Cilium validation schema from the selected RKE2 release.
- [x] Remove legacy task aliases and instructions after checking Flate coverage;
  preserve rendered resource counts with `flate:count`.
- [x] Replace Flux Local with Flate validation and rendered PR diffs, including
  complete text and HTML artifacts for large changes.
- [x] Add rendered resource and line-count overviews to all Flate diff comments,
  retain them for oversized diffs, and compare merge renders with their main parent.

- [ ] **Rotate GitHub automation tokens annually.** The `soupbot` Flux source token
  (`public_repo`) and commit-status token (`repo:status`) have no provider expiry.
  Rotate both by 2027-08-27, keeping the credentials separate and least-privileged.

- [ ] Renovate automerges only GitHub Actions and mise tools, so chart/container PRs
  pile up. Automerge patch-level helm/docker updates gated on the flate CI check.
- [ ] Majors needing human review: prometheus-operator-crds 30 (#76, CRD changes),
  actions/checkout v7 (#75, runner node version), error-pages 4 (#66, default
  backend), terraform ldap 0.13 (#74, provider under the data_json workaround),
  helm 4 (#23, conflicted, breaking CLI changes).

## Tooling

- [x] Fix Ansible test warnings: update `ansible.posix` to 2.2.0 for its
  [deprecated-import fixes](https://github.com/ansible-collections/ansible.posix/issues/686),
  and use the pinned linter's bundled schemas to avoid its unclosed HTTP 304 response.
  Correct the literal `~` in the workstation's Elixir PATH entry in Bash dotfiles.

- [x] Replace Hermes routing, profile credentials, and mandatory specialist gates with
  compact, explicitly invoked project skills and shared repository guidance.
- [ ] Retire the external Hermes runtime and GitHub webhook/App subscription on
  satellite. These are configured outside this repository; remove obsolete profile
  credentials and routing labels after confirming no other consumer needs them.

- [ ] Guard the completions in `.mise.toml` with a tool-exists check so a fresh
  `mise trust` does not spew errors for tools not yet installed.
- [ ] Find a first-party Flux way to visualize the Kustomization dependsOn order
  (topo-sorted tree) as a `task`. Prefer `flux tree` or similar over hand-rolled
  parsing.

## Log ingestion follow-up

- [ ] Simplify AIDE scheduling to Ubuntu's working `dailyaidecheck.timer`. The
  redundant `ansible-aidecheck` fails without a configuration path. Review runtime
  filesystem exclusions and the changed-file report before accepting a new baseline.
- [ ] Remove unused OpenIPMI startup on hosts without an IPMI interface. The current
  driver reports no compatible system interface.
- [ ] Group Java stack-trace continuation lines with their originating event at
  ingestion, preserving container boundaries and avoiding guessed severity.
- [ ] Reduce the OpenEBS ZFS snapshot controller's `--v=5` verbosity after confirming
  the chart setting. Routine watch lifecycle messages dominate its logs.
- [ ] Track Cilium's internal access-log UTF-8 handling. A synthetic HTTP header
  containing byte `0xff` reproduces a rejected protobuf record in 1.19.6 while
  forwarding and native JSON access logs succeed. The agent and proxy schemas match.
  [The proxy copies raw header values into string fields](https://github.com/cilium/proxy/blob/edeb3f2af56c37c407efa1f63f0b32f595399bbc/cilium/accesslog.cc#L194).
  Keep the independent JSON log pipeline and verify an upstream sanitization fix
  before changing versions. Do not suppress the warning or strip application headers.
- [x] Provision a Kubernetes-managed node overview with stacked per-app CPU and memory,
  host totals, disk utilization, ZFS state and network traffic using existing scrapes.
- [x] Label warning-prefixed messages conservatively while preserving their text.
- [x] Add inspected, guarded ZFS bootstrap-clone maintenance tasks. Refuse active
  recovery images and data-bearing origins.
- [x] Complete Cilium Gateway production cutover after dual-stack staging and backend
  isolation checks. Keep native Cilium Services for Minecraft TCP/UDP.
- [x] Verify the RKE2 1.35.8 upgrade live, including fresh dual-stack management access,
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

- [x] Retire etcd backup/restore in favor of clean GitOps control-plane rebuilds and
  application S3 recovery. Removed 147 etcd S3 objects/versions; both dedicated
  prefixes verified empty, including multipart uploads. Host backup services and
  local snapshots removed; the live datastore and application backups remain intact.
  Historical etcd work is preserved in Git history.
- [ ] Complete the operator-run clean cluster rebuild and validate recovered contents.
  Application-level restores are proven independently; the full uninstall, old-volume
  deletion and fresh bootstrap sequence remains to be exercised by the operator.

- [x] Validate `task volsync:replication-sync` live: trigger all scheduled restic
  sources, show progress, require fresh completion, and preserve schedules/hooks.
  All four sources completed with schedules preserved; a scoped run verified mover
  progress. Fresh LDAP snapshot `e9a1c0ff` passed isolated native restore/export
  comparison (89 entries); temporary Pod and plaintext were removed.

- [x] Run shared terminal-pod cleanup after bootstrap's Flux handoff, preserving
  Pending restores. Confirm the node shell is `kube:shell` and add `license:apache2`.

- [x] Replace the obsolete Talos/VPS egress proposal with current routing ownership
  and correct README/network topology references to the deployed Cilium Gateway.
- [x] Add `rke2:uninstall NODE=nas` using the upstream uninstaller, preceded by fresh
  backups and a maintenance hold. Live prepare/resume passed for all four sources;
  no uninstall was executed. LDAP snapshot `429e7903` passed native restore/export
  comparison (89 entries); temporary Pod/plaintext removed. Twenty-three behavior
  tests and Ansible syntax/lint checks passed.
- [x] Reconstruct Grafana LDAP provisioning state through imports after clean recovery,
  and stop recreating obsolete LDAP migration volumes on new clusters.

- [x] Make bootstrap cluster-only and unchanged on running RKE2. Live NAS invocation
  returned changed=0 before facts, credentials or cleanup. Add explicit old-OpenEBS
  deletion prompt and scope checks; read-only plan covers current/legacy ZFS PVCs and
  one hostpath volume. OS/network setup and pool creation remain separate.

- [x] Use Sofie’s hardware-backed SSH identity for host operations and remove the
  separate automation-account provisioning path. Deleted the obsolete NAS account,
  home and sudo rule; fresh Sofie SSH/sudo and the unchanged bootstrap path passed.

- [x] Remove Jellyfin configuration and media VolSync backups and restore dependencies
  by operator request; preserve live application and media PVCs.
- [ ] Run `docs/jellyfin-backup-cleanup.md` after deploying the no-backup policy to
  remove Jellyfin-only backup history and leftover VolSync resources.
- [x] Add `task volsync:restic APP=<app> -- <arguments>` with shared Kubernetes
  backup credentials scoped to that command, without local secret decryption.
