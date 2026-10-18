<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: MIT
-->

# TODO

Existing open follow-ups, with data-loss items first. Track new work in GitHub issues.
Remove entries when resolved or moved to an issue.

## Application reconciliation

- [ ] Verify a real Canaille photo in Grafana after signing out and back in.
  Gravatar remains disabled.

## Copyright attribution

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

- [ ] **Retire remaining router-local home.arpa names.** The API now uses upstream
  DNS. Inventory replacement records before removing DHCP-generated names used by
  other LAN devices, and migrate verified SSH host-key aliases independently.

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
- [ ] **PodSecurity: workloads violate `restricted`.** canaille lacked
  allowPrivilegeEscalation=false, drop ALL, runAsNonRoot, seccompProfile; likely
  repo-wide. Sweep all Deployments and chart values.
- [ ] **Return Canaille to upstream images after the profile form fix is released.**
  Track [upstream MR !351](https://gitlab.com/yaal/canaille/-/merge_requests/351).
- [ ] **Canaille token administration has two upstream UI bugs.** Version 0.3.3 still
  crashes after revoking client-credentials tokens because audit logging assumes a user
  subject, and its Copy buttons submit the surrounding form. Report and track upstream;
  do not carry local source patches.

## Flux / reconciliation

- [ ] **`task flux:reconcile` only kicks the root Kustomization.** Fine after a push
  (revision changes propagate), but it does not retry failed apps; `flux:reconcile-ks`
  does, serially and slowly. Consider a parallel variant or an alias that also
  reconciles cluster-apps.
- [ ] **Push detection is a 1m git poll.** A GitHub webhook into a Flux Receiver
  would make it instant, at the cost of exposing notification-controller publicly.
  Optional polish.
## Networking / future

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
## Renovate / PR hygiene

- [ ] **Rotate GitHub automation tokens annually.** The `soupbot` Flux source token
  (`public_repo`) and commit-status token (`repo:status`) have no provider expiry.
  Rotate both by 2027-08-27, keeping the credentials separate and least-privileged.

- [ ] Renovate automerges only GitHub Actions and mise tools, so chart/container PRs
  pile up. Automerge patch-level helm/docker updates gated on the flate CI check.

## Tooling

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
- [ ] Reduce idle CPU in the Minecraft and LDAP quiesce watchers. Their five-second
  kubectl polling currently consumes about 3% of one core each between backups.

- [ ] Show healthy configured alert rules scoped to the selected namespace and apps.
  Grafana’s native alert list filters labels on active instances, so inactive generic
  rules cannot currently be scoped this way. Keep the app view scoped and avoid
  duplicating alert definitions or changing alert evaluation for the dashboard.

- [ ] Complete the operator-run clean cluster rebuild and validate recovered contents.
  Application-level restores are proven independently; the full uninstall, old-volume
  deletion and fresh bootstrap sequence remains to be exercised by the operator.

- [ ] Run `docs/jellyfin-backup-cleanup.md` after deploying the no-backup policy to
  remove Jellyfin-only backup history and leftover VolSync resources.
