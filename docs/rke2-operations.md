<!--
SPDX-FileCopyrightText: 2026 Soup Bot <bot+ops@finnes.dev>

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

Do not install a second Cilium, CoreDNS or snapshot-controller release through Flux.
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

For emergency rollback, suspend the policy Kustomization and delete
`ciliumclusterwidenetworkpolicy/rke2-host-ingress` through the console's loopback API:

```bash
export KUBECONFIG=/etc/rancher/rke2/rke2.yaml
KUBECTL=/var/lib/rancher/rke2/bin/kubectl
$KUBECTL -n flux-system patch kustomization flux-system --type=merge \
  -p '{"spec":{"suspend":true}}'
$KUBECTL -n network patch kustomization cilium-host-policy --type=merge \
  -p '{"spec":{"suspend":true}}'
$KUBECTL -n flux-system patch kustomization cluster-apps --type=merge \
  -p '{"spec":{"suspend":true}}'
$KUBECTL delete ciliumclusterwidenetworkpolicy/rke2-host-ingress
```

Persist the suspension in Git before resuming `flux-system` and `cluster-apps`; otherwise reconciliation
reinstalls the policy. Diagnose the denied path before reenforcing it.

## Maintenance and recovery

Inspect `task ansible:check` before applying host configuration. Kernel reboots are
explicit maintenance; automatic security updates do not automatically reboot.

For RKE2, change `rke2_version` and `rke2_tarball_sha256` together. Verify application
backups and independent token custody, then run:

```bash
task ansible:upgrade -- -e rke2_recovery_verified=true
task ansible:firewall:verify
```

The upgrade refuses downgrades and skipped minors, saves and verifies an S3 etcd
snapshot, cordons the node, installs the checksum-verified release and waits for node
readiness before uncordoning. A failure after cordoning leaves the node cordoned for
inspection. Do not downgrade binaries against an upgraded datastore: restore the
matching pre-upgrade snapshot with the original token using the recovery runbook.

Verify Cilium/BGP, DNS, application contents and public TCP/UDP after maintenance.
The current RKE2 pin stays unchanged during this migration.

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
