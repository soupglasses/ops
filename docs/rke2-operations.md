<!--
SPDX-FileCopyrightText: 2026 Soup Bot <bot+ops@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# RKE2 host operations

The `home` cluster is a single RKE2 server on Ubuntu. Ansible owns the physical host,
RKE2, and the day-zero Cilium configuration. Flux owns Kubernetes applications and
Cilium policy resources after the API is available.

## Ownership boundary

| Owner | Resources |
| --- | --- |
| Ansible | accounts, SSH, systemd-networkd, host hardening, ZFS prerequisites, NVIDIA driver, RKE2 and its bundled Cilium chart values |
| RKE2 | etcd, control-plane static pods, containerd, CoreDNS and the bundled Cilium release |
| Flux | applications, OpenEBS, VolSync, Cilium BGP resources, LB-IPAM pools and workload policies |
| RouterOS Ansible | Kubernetes BGP peers, exact prefix filters and public IPv6 firewall rules |

Do not add a Flux Cilium `HelmRelease`. RKE2 must install the CNI before Flux can run;
two Helm owners would race over the same resources.

## Fixed network plan

| Purpose | IPv4 | IPv6 |
| --- | --- | --- |
| Node | `172.21.71.10/24` | `2a13:e745:1ee8:71::10/64` |
| Gateway | `172.21.71.1` | `2a13:e745:1ee8:71::1` |
| Pod CIDR | `10.42.0.0/16` | `fd94:a259:ebb8:4200::/56` |
| Service CIDR | `10.43.0.0/16` | `fd94:a259:ebb8:4300::/112` |
| Public service VIP | `172.21.68.10/32` | `2a13:e745:1ee8:68::10/128` |

The host uses native files under `/etc/systemd/network`; Netplan and cloud-init do not
own networking. Both node addresses are static. The RouterOS DHCP reservation for
`.10` remains only to prevent accidental allocation to another machine.

## Bootstrap and converge

Install the pinned collections, create the automation account through the temporary
installer account, and then converge the host:

```bash
cd ansible
ANSIBLE_LOCAL_TEMP=/tmp/ansible-local ansible-galaxy collection install -r requirements.yml
ANSIBLE_LOCAL_TEMP=/tmp/ansible-local ansible-playbook playbooks/bootstrap.yml \
  -e ansible_host=172.21.71.254 -e ansible_user=sofie \
  -e '{"automation_authorized_key":"ssh-ed25519 AAAA... ansible@controller", \
       "automation_controller_sources":["172.21.69.42/32","2a13:e745:1ee8:69::42/128"]}'
ANSIBLE_LOCAL_TEMP=/tmp/ansible-local ansible-playbook playbooks/site.yml \
  -e ansible_host=172.21.71.254 \
  -e '{"automation_authorized_key":"ssh-ed25519 AAAA... ansible@controller", \
       "automation_controller_sources":["172.21.69.42/32","2a13:e745:1ee8:69::42/128"]}'
ANSIBLE_LOCAL_TEMP=/tmp/ansible-local ansible-playbook playbooks/verify.yml
```

The network role reconnects at `.10`. Run it from the trusted LAN or WireGuard and
keep a local console available for the first handoff. Never disable SSH host-key
checking to make the transition pass. Replace the example controller addresses with its
stable addresses. The role fails closed unless it receives exactly one `/32` and one
`/128` in the trusted management subnet; record those non-secret values in inventory
after bootstrap so every convergence uses the same key boundary.

While audit mode is active, open a new SSH session and exercise API, monitoring, BGP,
backup, IPv4, and IPv6 paths while inspecting Hubble verdicts. Audit is the default and
direct enforcement fails unless the current host endpoint is already audited, both BGP
peers are established, fresh dual-stack SSH/API probes pass, and the lockout-risk phrase
is acknowledged. Promote and run the post-enforcement probes with:

```bash
task firewall:promote
```

TCP/22 stays limited to the trusted-LAN and WireGuard `/24` and `/64` networks plus the
controller's exact `/32` and `/128`. See `ansible/README.md` for console rollback and key
revocation commands.

`zfs_create_dataset` defaults to false. Enable it only after the imported Talos-era
datasets have been inventoried:

```bash
ANSIBLE_LOCAL_TEMP=/tmp/ansible-local ansible-playbook playbooks/site.yml \
  -e zfs_create_dataset=true \
  -e '{"automation_authorized_key":"ssh-ed25519 AAAA... ansible@controller"}'
```

The role may create `zfspool/k8s`; it never destroys a pool or old dataset.

## Secrets and recovery material

Do not put private SSH keys, RKE2 tokens, restic passwords, or S3 credentials in clear
text. New Ansible secrets belong in a newly created SOPS file and must be encrypted
exactly once according to `AGENTS.md`. The minimum independent recovery set is:

- every SOPS age identity and an offline recipient;
- restic repository password and S3 endpoint/credentials;
- `/var/lib/rancher/rke2/server/token`;
- an RKE2 etcd snapshot if etcd recovery, rather than GitOps rebuild, is desired;
- this repository and fetchable image/chart artifacts.

The operator must copy the RKE2 token to encrypted offline storage. Ansible deliberately
does not read or print it.

Both local and S3 etcd retention are explicitly 28 compressed snapshots at a six-hour cadence. This
is a nominal six-hour RPO and seven-day directly selectable history when every run succeeds. The S3
bucket must retain deleted/noncurrent versions for at least 30 days so delayed corruption is not
bounded by the rolling RKE2 window. Use `docs/rke2-s3-etcd-restore.md` for the version-matched clean-host
restore procedure and its strict ordering relative to Flux and VolSync.

## CIS posture

RKE2 runs with `profile: cis`, secret encryption, protected kernel defaults, and
restricted Pod Security Admission. Validate it with the release-matched RKE2 CIS 1.12
profile. Ubuntu receives the CIS-derived controls mapped in `docs/cis-control-mapping.md`.
Do not call the host formally Ubuntu-CIS certified until Canonical publishes and runs a
26.04 USG profile.

Privileged namespaces are exceptions, not the default. Keep Cilium in `kube-system` and
limit the repository's `privileged` component to infrastructure that demonstrably needs
it. Application namespaces must meet the restricted Pod Security Standard.

## Upgrade and reboot

Before an RKE2 or kernel change:

1. Confirm fresh successful VolSync backups and perform periodic restore drills.
2. Create and verify an etcd snapshot; ensure the RKE2 token is recoverable.
3. Change the pinned `rke2_version`; never follow an unpinned channel.
4. Run `playbooks/upgrade-rke2.yml`, one Kubernetes minor at a time.
5. Run `playbooks/verify.yml`, Cilium connectivity tests, BGP checks, and public TCP/UDP tests.

The cluster is single-node. A drain or reboot is planned downtime, not a highly
available rollout.

## S3 snapshot monitoring

Ansible installs a root-only systemd timer that runs `rke2 etcd-snapshot list` every
15 minutes using the S3 configuration already protected in
`/etc/rancher/rke2/config.yaml`. Raw command output is held briefly in a mode `0600`
file and discarded. Only the listing status, check time, newest scheduled snapshot
time, scheduled snapshot count, and configured retention target are written to the
node exporter's textfile directory. Bucket names, object names, endpoints, and errors
are never exported as metric labels or values.

Flux declares the private node-exporter endpoint, a `ServiceMonitor`, and alerts for a
missing signal, repeated listing failure, a snapshot older than ten hours, and a
retention deficit that persists beyond the seven-day warm-up needed for 28 snapshots
at the six-hour schedule. VictoriaMetrics Operator converts the Prometheus objects;
there is no Prometheus server or second runtime operator. VMAgent uses an exact label
selector, VMSingle keeps up to two days in ephemeral storage, and VMAlert evaluates
the rules and forwards them to VMAlertmanager. VMSingle stops ingestion before the node
has less than 5 GB free rather than letting monitoring exhaust the root filesystem.

The Prometheus Operator CRDs remain installed only as compatibility schemas for
repository-owned `ServiceMonitor` and `PrometheusRule` objects. VictoriaMetrics
Operator is the sole runtime reconciler and its converter produces the corresponding
`VMServiceScrape` and `VMRule` resources.

Metric history is useful operational context, but it is not authoritative application
data. Pod or node replacement may discard it by design; VMAgent repopulates current
signals automatically after the stack returns. Restore etcd and application data first.
Grafana is likewise stateless: its
VictoriaMetrics datasource and RKE2 snapshot dashboard are provisioned from Git, so its
SQLite database is intentionally excluded from the backup contract. Grafana also
installs the signed VictoriaLogs datasource plugin pinned to 0.32.0 and discovers its provisioned
datasource and dashboards after VictoriaLogs is reconciled.

VMAlertmanager sends firing and resolved notifications through
`prod-mta-06.swg-srv.net:2525` with required TLS to `alerts+ops@finnes.dev`, using
`alertmanager@noreply.finnes.dev` as its sender. Its authentication values arrive as
Helm values from the monitoring-owned `alertmanager-smtp` Secret; do not reuse an
application credential across namespaces. The raw Alertmanager configuration is stored
in a generated Kubernetes Secret so credentials are not exposed in a custom resource.

Verify the complete path after convergence:

```bash
sudo systemctl start rke2-etcd-snapshot-metrics.service
sudo systemctl status rke2-etcd-snapshot-metrics.timer
sudo grep '^rke2_etcd_s3_snapshot_' \
  /var/lib/prometheus/node-exporter/rke2-etcd-s3-snapshots.prom
curl --fail --silent http://172.21.71.10:9100/metrics \
  | grep '^rke2_etcd_s3_snapshot_'
kubectl -n monitoring get endpointslice,servicemonitor,prometheusrule \
  -l app.kubernetes.io/name=rke2-host-metrics
kubectl -n monitoring get vmservicescrape,vmrule,vmagent,vmsingle,vmalert,vmalertmanager
kubectl -n monitoring get pvc
kubectl -n monitoring port-forward \
  svc/vmagent-victoria-metrics-victoria-metrics-k8s-stack 8429:8429
# In another shell, confirm rke2-host-metrics is up at http://127.0.0.1:8429/targets.
kubectl -n monitoring port-forward \
  svc/vmalert-victoria-metrics-victoria-metrics-k8s-stack 8080:8080
# Confirm the snapshot rule group is healthy at http://127.0.0.1:8080/groups.
kubectl -n monitoring port-forward \
  svc/vmalertmanager-victoria-metrics-victoria-metrics-k8s-stack 9093:9093
# Confirm expected firing alerts appear at http://127.0.0.1:9093/#/alerts.
# Retrieve the local break-glass username and password for https://grafana.${DOMAIN}
# from the recovery password manager. Kubernetes reads the same values from the
# SOPS-encrypted grafana-auth Secret; do not print that Secret during routine checks.
```

A fresh timestamp and full retention count prove that RKE2 can list S3 objects; they
do not prove that a snapshot is restorable. Continue to require the documented clean
host S3 restore drill and application-level validation.

## Verification

After provisioning and after every reboot, require all of the following:

```bash
networkctl status enp33s0
systemctl --failed
systemctl status rke2-server
sudo zpool status -x zfspool
kubectl get nodes -o wide
kubectl -n kube-system get pods
cilium status --wait
cilium connectivity test
kubectl get ciliumbgpnodeconfigs.cilium.io -A
```

RouterOS must learn only the `/32` and `/128` service VIPs. Verify external HTTPS,
Minecraft TCP, voice-chat UDP, IPv4, and IPv6. A green controller or Ready pod is not
restore evidence.
