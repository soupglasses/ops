<!--
SPDX-FileCopyrightText: 2026 Soup Bot <bot+ops@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# RKE2 host management

This tree owns the Ubuntu 26.04 host beneath Flux: the automation identity, native
systemd-networkd configuration, CIS-derived OS baseline, non-destructive ZFS adoption,
NVIDIA prerequisites, and pinned single-server RKE2. Flux continues to own workloads and
post-bootstrap Cilium policy.

RKE2 runs its `cis` profile. The host prepares a default-deny input policy in audit mode, then requires
an explicit promotion after connectivity checks. It also applies AIDE, audit retention, password
policy, automatic security updates, SSH controls, and kernel hardening. Kubernetes requires documented
forwarding, eBPF, namespace, overlayfs, and ZFS exceptions. Ubuntu 26.04 is not claimed as formally CIS
certified until Canonical publishes a matching USG profile; the implemented and deferred controls are
tracked in `docs/cis-control-mapping.md`.

## Safety boundaries

- The ZFS role never creates, destroys, exports, rolls back, or mounts the existing pool or its old
  datasets. It imports `zfspool` with `-N`, keeps it out of the generic import cache, and prints the
  dataset inventory. A dedicated systemd unit repeats that non-mounting import after reboot.
- `zfspool/k8s` is created only with `zfs_create_dataset=true`. Existing datasets remain a temporary
  safety net; recover applications from off-cluster S3 backups into the new dataset and prove their
  contents before considering any pool recreation.
- Reformatting to RAIDZ2 requires a separate resilience review, successful application-level S3
  restore drills, an independently preserved rollback point, and explicit destructive approval.
  There is deliberately no destructive ZFS task here.
- The RKE2 token and backup credentials are secrets. Supply them from a new SOPS-encrypted vars file
  maintained by a human; never commit plaintext. The role does not print the supplied token.
- The first network change can interrupt SSH. Keep console access available. The play waits for
  `172.21.71.10` and switches the remaining connection to it.

## Install tooling

Use a Python virtual environment, then install the pinned controller and collections:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/ansible-galaxy collection install -r requirements.yml
```

Run commands from this directory so `ansible.cfg` is discovered.

## Bootstrap and converge

The committed inventory targets the final address. First create the automation account using the
temporary installer address and the public half of its dedicated SSH key:

```bash
ansible-playbook playbooks/bootstrap.yml \
  -u sofie -e ansible_host=172.21.71.254 \
  -e '{"automation_authorized_key":"ssh-ed25519 AAAA... ansible@controller", \
       "automation_controller_sources":["172.21.69.42/32","2a13:e745:1ee8:69::42/128"]}'
```

Use a dedicated automation key held in a hardware-backed agent or controller credential store. Never
put its private half in this repository, inventory, shell history, or on the managed server. The
installed key uses OpenSSH `from=` restrictions from `automation_controller_sources` plus `restrict`.
This variable fails closed by default: record exactly one stable `/32` and one `/128`, both inside the
trusted management subnet. Those exact hosts are added to the firewall policy; broader values are
rejected. Replace the example addresses above with the controller's real addresses before bootstrap.
`exclusive: true` removes replaced keys during rotation.

Immediately after bootstrap, record those two non-secret addresses in
`inventories/home/group_vars/all/main.yml`; subsequent plays intentionally fail while the default list
is empty.

The inventory pins SSH host-key verification to `nas.home.arpa`, whose key must already be present in
the controller's `known_hosts`. This preserves strict checking across the `.254` to `.10` transition;
verify the key out of band before adding it on a new controller.

Confirm console access. Then converge the host. On the first run, override the address so Ansible can
perform the network handoff:

```bash
ansible-playbook playbooks/site.yml \
  -u ansible -e ansible_host=172.21.71.254 \
  -e '{"automation_authorized_key":"ssh-ed25519 AAAA... ansible@controller"}'
```

Audit mode is the safe default. Before enforcement, inspect Hubble dropped/audited verdicts and prove
new sessions—not existing connections—for IPv4 and IPv6 SSH, Kubernetes API access, both authenticated
BGP peers, monitoring, backup, and application paths. TCP/22 is limited to the trusted-LAN and
WireGuard `/24` and `/64` networks rather than the full internal address space.

Promote only with working physical console access:

```bash
task firewall:promote
```

That explicit task supplies the required lockout-risk acknowledgement, converges the policy, and runs
`verify-host-firewall.yml`. The verification opens fresh authenticated SSH sessions and API sockets
over both address families, requires both router BGP peers to be established, and confirms that the
host endpoint is enforcing the Ansible-owned policy. `task firewall:verify` repeats those checks.

### Firewall rollback from the server console

If any promotion check fails, delete the enforcing policy first, then restore audit mode before further
diagnosis. These commands use the loopback API and therefore do not depend on network access to port
6443:

```bash
KUBECTL=/var/lib/rancher/rke2/bin/kubectl
KUBECONFIG=/etc/rancher/rke2/rke2.yaml
$KUBECTL --kubeconfig "$KUBECONFIG" delete \
  ciliumclusterwidenetworkpolicy/rke2-host-ingress --ignore-not-found
POD="$($KUBECTL --kubeconfig "$KUBECONFIG" -n kube-system get pod \
  -l k8s-app=cilium --field-selector spec.nodeName=nas \
  -o jsonpath='{.items[0].metadata.name}')"
ENDPOINT="$($KUBECTL --kubeconfig "$KUBECONFIG" -n kube-system exec "$POD" -- \
  cilium-dbg endpoint get -l reserved:host -o jsonpath='{[0].id}')"
$KUBECTL --kubeconfig "$KUBECONFIG" -n kube-system exec "$POD" -- \
  cilium-dbg endpoint config "$ENDPOINT" PolicyAuditMode=Enabled
$KUBECTL --kubeconfig "$KUBECONFIG" -n kube-system rollout restart daemonset/cilium
$KUBECTL --kubeconfig "$KUBECONFIG" -n kube-system rollout status \
  daemonset/cilium --timeout=300s
$KUBECTL --kubeconfig "$KUBECONFIG" get \
  ciliumclusterwidenetworkpolicy/rke2-host-ingress --ignore-not-found
```

The final command must print no policy after the Cilium restart. After access is restored, rerun
`playbooks/site.yml` with its default audit settings to restore audit mode and recreate the policy
safely, then use `verify.yml` to confirm `PolicyAuditMode=Enabled`. Do not retry enforcement until
Hubble audit verdicts explain and cover the failed path.

To revoke the automation identity immediately from the physical console or a separate administrator
session, terminate active sessions and make its key file unreadable:

```bash
sudo chmod 000 /home/ansible/.ssh/authorized_keys
sudo pkill -KILL -u ansible
```

Rotate the controller key, update `automation_authorized_key`, restore the file to mode `0600`, and
rerun `bootstrap.yml` from the independent administrator session. Never rely on `passwd -l` alone to
revoke public-key authentication.

Later runs use the committed `172.21.71.10` inventory directly. After reviewing the old datasets,
create only the new OpenEBS parent when authorized:

```bash
ansible-playbook playbooks/site.yml \
  -e zfs_create_dataset=true \
  -e '{"automation_authorized_key":"ssh-ed25519 AAAA... ansible@controller"}'
```

Before enabling GPU workloads, run `ubuntu-drivers list --gpgpu` on the node and set
`nvidia_driver_package` to an exact package from that output. Driver installation is deliberately
not guessed. Install the matching NVIDIA container toolkit repository/package only after validating
its Ubuntu 26.04 support; Kubernetes device-plugin resources remain Flux-owned.

## Configuration and secrets

Non-secret defaults live in `inventories/home/group_vars/all/main.yml`. A human operator must create
`inventories/home/group_vars/all/secrets.sops.yaml` once, with these top-level keys:

- `rke2_token`
- `rke2_etcd_s3_endpoint`
- `rke2_etcd_s3_region`
- `rke2_etcd_s3_bucket`
- `rke2_etcd_s3_access_key`
- `rke2_etcd_s3_secret_key`
- `cilium_bgp_ipv4_password`
- `cilium_bgp_ipv6_password`

After entering the real values, encrypt that new file exactly once:

```bash
sops --encrypt --in-place inventories/home/group_vars/all/secrets.sops.yaml
```

The pinned `community.sops` vars plugin decrypts it in memory on the controller. Never pass secrets
with `-e`, which exposes them to shell history and process listings. Follow the repository's SOPS
rules; existing encrypted files must never be edited or decrypted by automation agents. The
repository's path-specific SOPS rule encrypts every value in non-Kubernetes SOPS files, so these are
ordinary top-level Ansible variables; do not wrap them in Kubernetes `data` or `stringData`.

The role refuses to start RKE2 without a stable token, complete S3 etcd settings, and two distinct,
random, 32-80 character base64url-safe BGP passwords from that file. It streams the Secret definitions
to `kubectl apply -f -` only after API readiness, under `no_log`, leaving no rendered plaintext file.
The resulting `cilium-bgp-ipv4-auth` and `cilium-bgp-ipv6-auth` Secrets use the key `password` in
`kube-system`. The same encrypted variables are the single source for RouterOS peer authentication.
Local compressed snapshots run every six hours; S3 adds the independent copy needed for clean-host
recovery. Preserve the token separately because snapshot restore requires the same token used to
encrypt bootstrap data.

RKE2 is pinned to `v1.34.11+rke2r1` and configured at cluster creation with dual-stack pod and service
CIDRs. Its bootstrap Cilium values enable native routing, kube-proxy replacement, dual stack, BPF
masquerading, BGP control plane, Maglev, bandwidth manager/BBR, Envoy, Hubble, metrics, and the host
firewall in audit mode. DSR, transparent encryption, and ServiceMonitors stay off until their
dependencies and traffic paths have been proven. Ansible owns the day-zero host policy; Flux owns the Cilium BGP
peers, advertisements, LB-IPAM pools, and workload policies.

## Verification and maintenance

```bash
task test
task check
task apply
task verify
```

`verify.yml` checks networkd, auditd, RKE2, Kubernetes node readiness, both node address families,
Cilium rollout, and ZFS pool health through their user-visible interfaces. Continue with Cilium's
connectivity test, BGP route/withdrawal checks on RouterOS, S3 application restore drills, and the RKE2
CIS benchmark before declaring recovery complete.

For upgrades, select a pinned `rke2_version` and run `playbooks/upgrade-rke2.yml`. It takes a local
etcd snapshot before changing RKE2. Confirm a fresh independent backup and saved server token first;
upgrade one Kubernetes minor at a time and run the full verification afterward.

## RouterOS

Bootstrap the dedicated RouterOS identity once through the existing administrator,
then use the committed `ansible` inventory identity. First reserve or statically assign
`routeros_management_source_ipv4` to the controller; the bootstrap assigns that `/32` to the
automation user's RouterOS `address` property before it enables the account.

```bash
ansible-playbook playbooks/bootstrap-router.yml -u 'admin+cet1024w' \
  -e routeros_automation_public_key_file="$HOME/.ssh/ansible.pub"
```

Keep the private key in the controller's SSH agent or protected key store; it never belongs in this
repository or SOPS. The account receives only `ssh,read,write,test`. RouterOS cannot scope `write`
to BGP and firewall paths, so it is unavoidable for convergence; the account is denied
`local,telnet,ftp,reboot,policy,winbox,password,web,sniff,sensitive,api,romon,rest-api`. Unique
comments and exact selectors limit the role's intended scope, while the source `/32`, SSH key, and
absence of password login constrain access. RouterOS firewall rules cannot match a username, so the
role does not add a router-wide SSH drop that could destroy the independent admin or WireGuard
recovery path.

The real encrypted inventory file contains the RKE2/S3 values plus distinct top-level
`cilium_bgp_ipv4_password` and `cilium_bgp_ipv6_password` entries. A human
operator updates an existing encrypted file through their SOPS editor; automation agents must not
decrypt or edit it. For initial creation only, populate the new file in an editor and encrypt it once:

```bash
sops --encrypt --in-place inventories/home/group_vars/all/secrets.sops.yaml
ansible-playbook playbooks/router.yml
ansible-playbook playbooks/verify-router.yml
```

The SOPS vars plugin supplies both values in controller memory. Secret-carrying includes and commands
are `no_log`; never supply either value through `-e`. RKE2 materializes the same values under key
`password` in `cilium-bgp-ipv4-auth` and `cilium-bgp-ipv6-auth`; the matching
`CiliumBGPPeerConfig.authSecretRef` fields select those Secrets. RKE2 applies them from controller
memory without leaving a rendered plaintext manifest on disk.

### Router access recovery and rotation

Keep an independent full administrator path and a recent encrypted RouterOS export before bootstrap.
Immediate automation revocation from that path is:

```routeros
/user disable [find where name="ansible"]
/user/ssh-keys remove [find where user="ansible"]
```

Restore the firewall/export through the independent administrator or physical console; do not rely
on the automation key being revoked. For a key rotation, choose a new unique
`routeros_automation_key_owner` and public-key file, run bootstrap to add it beside the old key, test
`playbooks/router.yml` using the new private key, and only then set
`routeros_automation_retire_key_owner` to the old owner for a second bootstrap run. Keeping the old
key until the smoke test is the rollback path.

### BGP password rotation

RouterOS and Cilium do not support overlapping TCP-MD5 passwords, so schedule a brief route
withdrawal. Preserve the previous encrypted Git revision, update the selected family password to a
new 32--80 character value, and increment its matching `cilium_bgp_*_auth_generation`. The generation
is the non-secret convergence marker because RouterOS never returns the sensitive password. Apply
RouterOS, reconcile the Cilium Secret/peer configuration,
then run `playbooks/verify-router.yml`. Also test public HTTPS, Minecraft TCP 25565, and voice UDP
24454 over both address families. Rotate one family at a time: update its encrypted inventory value and
generation, run `playbooks/router.yml` to update RouterOS, immediately run `playbooks/site.yml` to apply
the matching Ansible-owned `kube-system` Secret, and verify that family before rotating the other. If a
session or route does not return, restore the prior encrypted value and generation, rerun the router and
site playbooks in that order, and verify both sessions and exact VIP routes before reopening the
maintenance window.

### Emergency BGP credential revocation

From the independent RouterOS administrator path, disable both peers first so the `/32` and `/128`
are withdrawn immediately:

```routeros
/routing/bgp/connection disable [find where name="cilium-bgp"]
/routing/bgp/connection disable [find where name="cilium-bgp-v6"]
/routing/bgp/session print detail
/ip/route print detail where dst-address=172.21.68.10/32
/ipv6/route print detail where dst-address=2a13:e745:1ee8:68::10/128
```

From the server console, revoke the in-cluster copies without displaying either value:

```bash
/var/lib/rancher/rke2/bin/kubectl --kubeconfig /etc/rancher/rke2/rke2.yaml \
  -n kube-system delete secret cilium-bgp-ipv4-auth cilium-bgp-ipv6-auth --ignore-not-found
```

A human operator then replaces both values in the encrypted Ansible inventory and increments both
non-secret authentication generations. Run `playbooks/site.yml` to create the replacement Secrets,
then `playbooks/router.yml` to update and re-enable the peers. Do not re-enable them manually. Run
`playbooks/verify-router.yml`, confirm only the exact service routes returned, and test public traffic
over both families. If the controller key may also be compromised, keep the peers disabled and use the
automation-user revocation procedure above before installing new credentials.
