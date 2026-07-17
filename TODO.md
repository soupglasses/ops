<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# TODO

Findings from the July 2026 architecture review, ranked. Data-loss items first.

## Backups / disaster recovery

- [ ] **OpenLDAP data is not backed up.** IN PROGRESS. `openldap-data` is 1Gi on
  `openebs-hostpath`: node-local, on the EPHEMERAL partition, not snapshottable, no
  volsync. `talos:rebootstrap` destroys every human account and password; only system
  users are reconstructable via tofu. Wiring is committed; live migration steps are in
  `docs/openldap-volsync-migration.md`.
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
- [ ] **Restore destroys the only local copy before verifying.** volsync uses
  `openebs-zfs` with the default Delete reclaim; `scripts/volsync-restore.sh` deletes
  the PVC (and thus the ZFS dataset) before the repopulated volume is proven good.
  Take a VolumeSnapshot in the script before deleting the PVC.
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

- [ ] **Cilium is outside GitOps and unpinned.** Installed by an inline Talos job
  running `quay.io/cilium/cilium-cli:latest` (`talos/patches/cilium.yaml`); config
  changes need a machine-config re-apply. Keep a minimal day-0 inline install, then
  manage Cilium as a Flux HelmRelease that takes over. Prerequisite for enabling
  Gateway API support.
- [ ] **Pod CIDR mismatch (verified live).** Pods run in 10.244.0.0/16 (Talos
  default; `talconfig.yaml` sets no podSubnets) but Cilium has
  `ipv4NativeRoutingCIDR=10.42.0.0/16` and RouterOS BGP filters accept 10.42.0.0/16.
  It works only because mismatched traffic is masqueraded. Set podSubnets explicitly
  and align Cilium + RouterOS to it.
- [ ] **Talos/K8s upgrades pending, must be walked.** Node runs Talos 1.11.6 /
  K8s 1.34.2; Renovate PRs #33 (installer 1.13.5) and #29 (kubelet 1.36.2) must not
  be merged blind. Upgrade one minor at a time (1.11 -> 1.12 -> 1.13,
  1.34 -> 1.35 -> 1.36) as deliberate sessions, merging the config bump with each hop.
- [ ] **Node IP relies on a DHCP reservation.** `talconfig.yaml` uses `dhcp: true`
  while BGP peering, RouterOS DNAT, and CoreDNS all assume 172.21.69.10. Pin the
  address statically in machine config.
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
- [ ] **Push detection is a 1m git poll.** A Codeberg webhook into a Flux Receiver
  would make it instant, at the cost of exposing notification-controller publicly.
  Optional polish.
- [x] **cilium-bgp ks.yaml declares `namespace: kube-system`** but the parent
  kustomization rewrites it into `network`, where it actually lands. The declared
  namespace is misleading; align it.

## Networking / future

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
