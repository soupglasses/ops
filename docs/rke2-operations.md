<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# RKE2 cluster operations

The home cluster is one Ubuntu 26.04 server. Maintenance and reboot cause downtime.

| Owner | Configuration |
| --- | --- |
| Ansible | Host accounts, security baseline, explicit network setup, ZFS prerequisites, pinned RKE2 |
| RKE2 | etcd, containerd, Cilium, CoreDNS, metrics-server, snapshot controller and CRDs |
| Flux | Applications, OpenEBS, VolSync, BGP advertisements and network policies |
| RouterOS | VLANs, routing, BGP peers, perimeter firewall and WireGuard |

The host role and upgrade playbook maintain a non-secret
`/etc/rancher/rke2/config.yaml.d/20-cilium-ingress.yaml` drop-in with
`ingress-controller: [none]`. RKE2 1.36 otherwise selects Traefik when no
ingress-nginx HelmChart exists, including clusters already using Cilium Gateway.
This preserves Flux-managed ingress without rereading or rewriting credentials
during maintenance. See [RKE2 ingress selection](https://github.com/rancher/rke2/blob/v1.36.4%2Brke2r1/pkg/bootstrap/bootstrap.go).

Do not install a second Cilium, CoreDNS or snapshot-controller release through Flux.
RKE2 already installs Cilium through Helm. Keeping its bundled chart gives a single
owner and restores networking before Flux starts. Independent Cilium releases are
possible with `cni: none` and an explicitly pinned bootstrap `HelmChart`; Cilium
[documents that pattern for new Rancher-managed clusters](https://docs.cilium.io/en/stable/installation/k8s-install-rancher-existing-nodes/).
Adapting it here would require a separately reviewed ownership migration and clean
rebuild test. Installing a second release or treating the current RKE2 chart as a
one-time bootstrap does not transfer ownership safely.

CoreDNS's LDAP rewrite is declared in `kubernetes/bootstrap/rke2-coredns.yaml` and
installed as an RKE2 `HelmChartConfig` by the host role.

## Installation and recovery prerequisites

Use [the Ansible commands](../ansible/README.md) to bootstrap the account, configure
networking explicitly, and converge the host. Preserve these addresses:

| Purpose | IPv4 | IPv6 |
| --- | --- | --- |
| Node | `172.21.71.10/24` | `2a13:e745:1ee8:71::10/64` |
| Gateway | `172.21.71.1` | `2a13:e745:1ee8:71::1` |
| Pods | `10.42.0.0/16` | `fd94:a259:ebb8:4200::/56` |
| Services | `10.43.0.0/16` | `fd94:a259:ebb8:4300::/112` |
| Public VIP | `172.21.68.10/32` | `2a13:e745:1ee8:68::10/128` |

Keep an independent human login and console access. Record verified SSH host keys,
router administrator access, repository access, SOPS identities, restic passwords,
S3 endpoints/credentials and the original RKE2 server token in off-cluster recovery
storage. Keep a repository copy and record the pinned release/checksum and required
image/chart sources. Verify those artifacts remain fetchable during recovery drills.

Never overwrite legacy datasets to make space for a restore. Provision additional
storage if the empty recovery parent lacks room.

## Local Kubernetes access

The API endpoint is `https://k8s.home.finnes.dev:6443`. Upstream DNS resolves it to
`172.21.71.10`; access requires the home network or VPN. The name must appear in
`rke2_tls_sans` before clients use it.

From the repository root:

```bash
mise exec -- task kube:kubeconfig
mise exec -- kubectl get pods -A
```

The task fetches the RKE2 administrator configuration over verified SSH and checks
API access before replacing `.secure/kubeconfig`. It keeps a backup of the existing
file. Mise selects this configuration unless `HERMES_KUBECONFIG` overrides it.
Repeat the task after a clean rebuild to refresh the CA and client certificate.

Bootstrap SSH connects to the node IP using the verified `nas.home.finnes.dev`
host-key alias, so it remains available during DNS outages.

## Flux bootstrap

With the recovery age identity available to SOPS, run:

```bash
task ansible:verify
task flux:install
task flux:bootstrap:import-secrets
task flux:bootstrap
```

The import task streams these encrypted sources through SSH into namespace
`flux-system`:

| Secret | Source |
| --- | --- |
| `flux-system` | `kubernetes/clusters/home/secret.sops.yaml` |
| `sops-age` | `kubernetes/components/common/sops/secret.sops.yaml` |

Both imports must report created, configured or unchanged before bootstrap.
Keep shell tracing disabled while importing credentials. Bootstrap requires both
Secrets and the bundled snapshot controller. It installs the committed Gateway API
CRDs, waits for them to become established, and restarts the Cilium operator before
applying the Flux synchronization configuration. Cilium 1.19 discovers these CRDs
at operator startup, so the restart is required on a clean cluster where Cilium
starts before Flux. Subsequent CRD management belongs to Flux. Ordinary
`task flux:reconcile` does not restart the operator.

For recovery from an etcd snapshot, follow [etcd recovery](rke2-s3-etcd-restore.md).

## Application recovery

Recover infrastructure before applications, and LDAP before its consumers. For a
staged recovery, suspend application Kustomizations in Git until their prerequisites
are verified; a live `flux resume` cannot override a suspension declared in Git.

1. Verify Flux infrastructure, OpenEBS and VolSync, then validate dual-stack BGP and VIP
   routing with [the router checks](routeros-bgp-config.md).
2. Inventory each application's off-cluster snapshots and select a known-good point.
   A missing backup, listing error or wrong repository must stop recovery. VolSync can
   report success with an empty restore; inspect snapshot history and application
   contents, not just mover status. LDAP and Minecraft refuse to start without their
   database/world files.
3. For each application, commit its writer stopped and its ReplicationSource paused
   before removing the Kustomization suspension. Use Deployment replicas `0` for
   OpenLDAP and HelmRelease `spec.suspend: true` for Minecraft/VictoriaLogs. Set
   ReplicationSource `spec.paused: true` through that application's Kustomize patch.
   Temporarily remove application readiness health checks while writers are stopped.
4. Reconcile the RD and PVC, with the chosen `restoreAsOf` set before the initial
   restore when an older point is required. Validate the restored files using an
   isolated pod with matching UID/GID. Preserve the recovery point and evidence.
5. Start OpenLDAP, validate DNs/schema and login, then enable Canaille and LDAP
   provisioning. Validate Minecraft world/player data and voice traffic. Validate
   existing VictoriaLogs contents before allowing ingestion.
6. Restore normal application health checks and backup scheduling in Git. Trigger a
   backup and restore it into an isolated PVC; also demonstrate an older recovery
   point. Record content checks, recovery duration and backup timestamps.
7. Remove all disposable pods/PVCs/CRs. Preserve rollback snapshots until content
   validation succeeds. Keep legacy ZFS datasets and Talos recovery files intact.

For a genuinely new application, create and validate its initial dataset explicitly,
back it up to its new restic repository, then enable the normal restore-on-create
component. Do not bypass recovery by initializing an application with missing history.

### Metrics and log recovery

Vector collects Kubernetes container logs, including VictoriaMetrics and VictoriaLogs,
and the host journal into VictoriaLogs. The collector runs in `log-collection`, with
read-only access to host logs; the database and dashboards remain in `monitoring`.

VictoriaMetrics and VictoriaLogs retain one year of data on HDD-backed `openebs-zfs`
volumes beneath `zfspool/k8s`. Start with 100 GiB for metrics and 200 GiB for logs;
both volumes can expand. Low-space alerts fire below 20% free, and no disk-pressure
policy deletes old history. Restic deduplicates backup content across recovery points.

VolSync sends encrypted restic backups to the existing S3 bucket every six hours:
logs at minute 17 and metrics at minute 37. It retains seven daily and four weekly
recovery points, each containing the retained application history. Node loss can lose
data collected since the last successful backup.
[VictoriaLogs supports filesystem snapshots](https://docs.victoriametrics.com/victorialogs/#backup-and-restore),
which lets this use the same ZFS/VolSync recovery path as other applications.

A clean rebuild restores the newest backup through the PVC's VolSync data source.
On a replacement cluster, install Flux, VolSync and a CSI driver with snapshot support,
import the existing recovery credentials, and set the `VOLSYNC_STORAGECLASS`,
`VOLSYNC_SNAPSHOTCLASS` and `VOLSYNC_CACHE_SNAPSHOTCLASS` substitutions for that cluster.
Restic restores the files from S3 onto the replacement storage.
Retain the application namespaces and repository names so the existing credentials
and backups resolve correctly. The database startup checks reject empty restores.

For corruption recovery on a replacement cluster, select a known-good point before
the ReplicationDestination is first created. Add this patch to the application's
`app/kustomization.yaml`, using the desired UTC recovery timestamp:

```yaml
patches:
  - target:
      kind: ReplicationDestination
    patch: |-
      - op: add
        path: /spec/restic/restoreAsOf
        value: "2026-09-26T16:47:00Z"
```

Keep the ReplicationSource paused during validation. VolSync selects the most recent
backup at or before that timestamp. Existing destinations use `IfNotPresent` ownership
and do not pick up this patch retroactively; use a fresh recovery destination and PVC.
Preserve the original cluster or volume until native queries confirm usable recovered
history. Remove the timestamp patch and resume backups after accepting the recovery.

Before restoring history older than one year, increase the database retention in Git;
otherwise startup can discard that history. Restore checks should query known records
from both monitoring services before accepting the recovery.

## Host firewall handoff

Cilium's endpoint audit flag is temporary: an agent restart reenables enforcement.
Do not rely on an inventory default to infer live mode. The migration preserves the
existing `rke2-host-ingress` policy while Ansible relinquishes ownership.

With console access and working human recovery login, inspect Hubble verdicts and
prove new SSH/API connections over both families, both BGP sessions, monitoring,
backup and application traffic. The pending Flux policy is in the suspended
`network/cilium-host-policy` Kustomization. After those checks and security review,
remove its suspension in Git and reconcile it. Do not delete the existing policy
before adoption. Ansible no longer writes the policy or changes audit mode.

Run `task ansible:firewall:verify`, restart Cilium during the maintenance window,
then repeat the checks. Repeat after a controlled host reboot and verify applications
and ZFS. Remove `/etc/rancher/rke2/host-firewall.yaml` only after Flux adoption is proven;
it is an obsolete local copy, not the active policy.

Before a firewall experiment, verify fresh management connections over both address
families and capture the working policy from a root shell on nas. The checkpoint
contains policy configuration, not secrets. Keep it root-only and preserve its
management source exceptions.

```bash
export KUBECONFIG=/etc/rancher/rke2/rke2.yaml
KUBECTL=/var/lib/rancher/rke2/bin/kubectl
umask 077
$KUBECTL get ciliumclusterwidenetworkpolicy/rke2-host-ingress -o json |
  python3 -c '
import json, sys
policy = json.load(sys.stdin)
checkpoint = {key: policy[key] for key in ("apiVersion", "kind", "spec")}
checkpoint["metadata"] = {"name": policy["metadata"]["name"]}
json.dump(checkpoint, sys.stdout)
' > /root/rke2-host-ingress.before.json
chmod 600 /root/rke2-host-ingress.before.json
```

Suspend the parent reconcilers and policy Kustomization before experimenting or
restoring through the console's loopback API. This prevents reconciliation from
replacing the checkpoint during recovery.

```bash
$KUBECTL -n flux-system patch kustomization flux-system --type=merge \
  -p '{"spec":{"suspend":true}}'
$KUBECTL -n flux-system patch kustomization cluster-apps --type=merge \
  -p '{"spec":{"suspend":true}}'
$KUBECTL -n network patch kustomization cilium-host-policy --type=merge \
  -p '{"spec":{"suspend":true}}'
```

For a remote experiment, arm a local timed restore before changing the policy:

```bash
systemd-run --unit=rke2-host-policy-rollback --on-active=5m \
  "$KUBECTL" --kubeconfig /etc/rancher/rke2/rke2.yaml \
  apply -f /root/rke2-host-ingress.before.json
```

If access fails, let the timer restore the checkpoint or apply it immediately
through the physical console:

```bash
/var/lib/rancher/rke2/bin/kubectl --kubeconfig /etc/rancher/rke2/rke2.yaml \
  apply -f /root/rke2-host-ingress.before.json
```

Keep the host policy installed and enforcing throughout recovery. Verify fresh
IPv4/IPv6 SSH and API connections, BGP and application traffic before cancelling a
pending timer with `systemctl stop rke2-host-policy-rollback.timer`. Correct the
policy in Git and preserve its suspension until the corrected version has been
reviewed. Then resume reconciliation deliberately and repeat the checks. Gateway
migration alone does not require changing the host policy.

## Maintenance and recovery

Inspect `task ansible:check` before applying host configuration. Kernel reboots are
explicit maintenance; automatic security updates do not automatically reboot.

For RKE2, change `rke2_version` and `rke2_tarball_sha256` together. Verify application
backups and independent token custody, then run:

```bash
task ansible:upgrade -- -e rke2_recovery_verified=true -e rke2_upgrade_reviewed=true
task ansible:firewall:verify
```

Follow the [RKE2 manual upgrade guide](https://docs.rke2.io/upgrades/manual):
read the selected release's RKE2 and Kubernetes urgent upgrade notes, retain the
installation method and configuration, and advance one Kubernetes minor at a time.
The playbook uses the supported tarball/binary replacement followed by a service
restart, reloading systemd for packaged unit changes. It only supports the current
single amd64 server; a future multi-node procedure must upgrade servers serially
before agents. A cordon prevents new scheduling but does not evict existing pods.
We deliberately do not drain the only node: there is no alternate capacity and
forcing eviction would unnecessarily stop applications or discard ephemeral data.
Expect API and network downtime and keep console access throughout maintenance.
Run only one maintenance process; do not change Cilium configuration concurrently.

Before setting `rke2_upgrade_reviewed=true`, inspect the bundled Cilium version and
Gateway API requirements, chart value changes, deprecated APIs, and the target
Kubernetes compatibility matrix. A grouped Renovate PR does not prove compatibility.
Gateway CRDs deploy through Flux as soon as merged; RKE2 changes only during explicit
maintenance. Split the proposal or stage compatible CRDs first if the current and
target Cilium cannot both support the proposed CRDs. Never merge an incompatible
Gateway change while waiting to upgrade the node.

When bundled Cilium changes, follow its
[upgrade guide](https://docs.cilium.io/en/stable/operations/upgrade/):

1. Upgrade through consecutive Cilium minors and reach the latest patch of the
   current minor before crossing to the next. Select an intermediate RKE2 release
   carrying that patch if needed; do not replace RKE2's managed Cilium release.
2. Review version-specific notes and preserve the installed `HelmChartConfig`
   settings, including dual-stack routing, BGP, host firewall and Gateway API.
   Review `upgradeCompatibility` for the original installed Cilium minor; make any
   needed values change through the managed host configuration before maintenance.
3. Run the target version's documented Cilium preflight with the actual API endpoint
   and kube-proxy replacement settings. Wait for both the preflight DaemonSet and
   policy-validation Deployment to be ready. Confirm the RKE2 chart's mirrored
   target images are pullable too. Remove all temporary preflight resources.
4. Only after these checks, append `-e rke2_cilium_preflight_verified=true` to the
   upgrade command. The playbook resolves the bundled chart's application version
   and refuses Cilium downgrades, skipped minors, or a changed version without this
   acknowledgment. It does not assume Kubernetes patch upgrades leave Cilium unchanged.

The upgrade first rejects unhealthy nodes, pre-existing cordons, unexpected cluster
members and installed/running version mismatches. It downloads and verifies the
release before saving and checking a new S3 etcd snapshot, then cordons and restarts.
Node readiness and the requested kubelet version permit uncordoning so RKE2's Helm
jobs and operator can schedule. The playbook then checks the embedded target chart in both RKE2 and the Helm
controller ConfigMap, a completed Helm job created after chart-content publication (and a replacement
job when the chart changes),
agent/operator image versions, agent/operator/Envoy rollouts and Cilium health before
running host verification. A network-rollout failure attempts to re-cordon and stops;
API or SSH loss may require doing this through the console. Host verification failures
occur after uncordoning and require operator inspection. There is no automatic rollback.

The task does not load encrypted inventory or reconverge host and Cilium configuration.
An already-installed release still gets Cilium and host verification without another
snapshot or restart. If an upgrade was interrupted, preserve its original pre-upgrade
snapshot; inspect `systemctl status rke2-server` and `kubectl get node nas`, complete
the restart and verification, then uncordon deliberately. Follow
[RKE2 rollback guidance](https://docs.rke2.io/upgrades/roll-back) and the recovery
runbook to restore the matching old release and snapshot with the original token;
never downgrade binaries against an upgraded datastore.

Verify Cilium/BGP, DNS, application contents and public TCP/UDP after maintenance.
The 2026-09-26 upgrade validated [RKE2 v1.35.8+rke2r1](https://github.com/rancher/rke2/releases/tag/v1.35.8%2Brke2r1),
one Kubernetes minor above the recovered cluster. That release retains Cilium 1.19.6, etcd
3.6.14, containerd 2.2.7 and the snapshot-controller chart, keeping the networking
and storage rollout separate from the control-plane upgrade. Cilium 1.19.6
[tests Kubernetes 1.35](https://github.com/cilium/cilium/blob/v1.19.6/Documentation/network/kubernetes/compatibility.rst).
Keep Cilium managed by RKE2 and advance it with a compatible bundled release.

The inventory checksum matches the release's `sha256sum-amd64.txt` and GitHub
artifact digest. Renovate updates the version and checksum together in a reviewed
platform proposal. The validation schema follows bundled Cilium automatically. Check Cilium/Gateway
API compatibility before merging. See [dependency updates](dependency-updates.md).
A changed inventory pin does not upgrade an installed node through normal convergence.

Etcd snapshots run every six hours, retaining 28 locally and in S3. Independent S3
history must preserve deleted/noncurrent objects for at least 30 days and prevent the
writer from permanently erasing the only copy. Application retention remains unchanged.
The host exports aggregate backup freshness metrics; prove both firing and resolved
notifications reach the off-cluster recipient. A Ready pod or successful backup job
alone is not restore evidence.

Recovery procedures: [etcd](rke2-s3-etcd-restore.md), [ZFS and application data](zfs-s3-recovery.md),
[LDAP contents](openldap-volsync-migration.md). RAIDZ2 reformatting remains separate.

References: [RKE2 upgrades](https://docs.rke2.io/upgrades/manual),
[RKE2 restore](https://docs.rke2.io/datastore/backup_restore),
[Cilium host firewall](https://docs.cilium.io/en/stable/security/host-firewall/).
