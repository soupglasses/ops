<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# Rebuild the Kubernetes cluster from S3 backups

This procedure replaces the cluster on an already prepared host. It does not reinstall
Ubuntu or reconfigure accounts, networking, disks, host hardening or GPU drivers.
RKE2 starts with fresh state; Flux recreates applications from Git, and VolSync restores
new volumes from S3. There is no etcd restore path.

`task rke2:bootstrap` changes nothing when RKE2 is already running or transitioning.
It returns before gathering facts, loading credentials, prompting or touching storage.
To deliberately rebuild a running cluster, uninstall it first as described below.

## 1. Prepare your workstation and recovery material

Run commands from this repository on your trusted workstation. Install its tools and
Ansible environment if needed:

```bash
mise install
python3 -m venv ansible/.venv
ansible/.venv/bin/pip install -r ansible/requirements.txt
export PATH="$PWD/ansible/.venv/bin:$PATH"
task ansible:deps
```

Use the repository's mise environment when running `task`. Have these available
independently of the cluster before removing it:

- The repository, including the existing encrypted host inventory and Kubernetes Secrets.
- The SOPS recovery identity capable of decrypting those files, supplied through your
  normal protected operator environment.
- The SSH key and sudo access for the inventory host.
- Access to the S3 application repositories, with their existing restic passwords and
  credentials recoverable from the encrypted cluster configuration.
- Access to the pinned images and charts or a tested artifact mirror. VolSync restores
  volume contents, not container images.

The repository contains encrypted credentials; it does not replace the independent
key that decrypts them. Bootstrap uses that key internally and suppresses secret output.
Do not place secret values in command arguments or shell history.

Review the inventory before starting:

| File | Purpose |
| --- | --- |
| `ansible/inventories/home/hosts.yml` | Host address, SSH account/key and verified host-key alias |
| `ansible/inventories/home/group_vars/all/main.yml` | RKE2 settings, existing ZFS pool and Kubernetes dataset parent |
| `ansible/inventories/home/group_vars/all/secrets.sops.yaml` | Encrypted cluster token and BGP credentials |

NAS is already prepared. For a replacement OS, perform the separate host setup in
[the Ansible guide](../ansible/README.md) first. This cluster bootstrap requires the
configured ZFS pool and dataset parent to exist; it never formats disks or creates a
pool. Keep console access available during the rebuild.

## 2. Verify backups and uninstall the old cluster

Before destroying local volumes, validate application restores and independent recovery
keys. Existing [restore evidence](rke2-recovery-verification.md) covers the application
recovery mechanisms; a successful upload alone does not establish restorability.

For a visible backup run before the maintenance window:

```bash
task volsync:replication-sync
```

Then uninstall RKE2 on the inventory node:

```bash
task rke2:uninstall NODE=nas
```

The uninstall task completes fresh application backups again, holds their acknowledged
manual triggers and Flux writers, and waits for mover cleanup before invoking RKE2's
supplied `/usr/local/bin/rke2-uninstall.sh`. New scheduled backups cannot race teardown.
Preparation failure aborts uninstall and attempts to restore schedules and Flux.

[RKE2's uninstaller](https://docs.rke2.io/install/uninstall) has no last-server guard or
force flag. It removes the selected node's RKE2 installation and state. The task does
not invent a replacement teardown algorithm. It preserves the S3 repositories and
leaves OpenEBS volume deletion to the bootstrap's explicit prompt.

## 3. Bootstrap and confirm the clean storage slate

```bash
task rke2:bootstrap
```

On a stopped host, bootstrap refuses leftover RKE2 server state. If uninstall failed,
resolve that failure rather than deleting arbitrary state to bypass the check.

Before starting RKE2, bootstrap inventories and displays the OpenEBS volumes to erase:

- PVC datasets beneath the configured Kubernetes ZFS parent, including snapshots and clones.
- Legacy PVC datasets directly beneath the pool root.
- Legacy OpenEBS hostpath PVC directories under `/var/openebs/local`.

For the current inventory, confirm the displayed plan by typing:

```text
DELETE zfspool/k8s
```

This permanently removes those old local volumes, including migration rollback copies.
The pool, its empty Kubernetes parent and unrelated datasets are preserved. Unexpected
names, mounted volumes, external clone dependencies or changed volume identities stop
cleanup. The helper rechecks the plan and verifies no selected volumes remain before
RKE2 starts. If storage is already empty, there is nothing to delete or confirm.

RKE2 is then installed from the inventory's pinned release. Flux imports the existing
Git/SOPS credentials and rebuilds the declared applications. Fresh PVCs reference
VolSync restore destinations; application startup guards reject missing directory,
world or database data. Existing data volumes are never adopted as recovery input.

Bootstrap finishes its Flux handoff with terminal-pod cleanup, preserving Pending pods
that may still be waiting for restores. Application recovery continues asynchronously.
There are no `RESTORE`, `SNAPSHOT` or `MODE` options.

Pass Ansible options after `--` when overriding the inventory connection, for example:

```bash
task rke2:bootstrap -- -i inventories/home/hosts.yml \
  -e ansible_user=sofie \
  -e ansible_ssh_private_key_file="$HOME/.ssh/gpg.pub"
```

The playbook runs from `ansible/`, so inventory paths above are relative to that directory.
Use the same connection overrides for uninstall if the default automation key is not
available. Bootstrap does not perform a network-address handoff.

## 4. Watch and validate recovery

Fetch a fresh administrator kubeconfig:

```bash
task kube:kubeconfig
```

Watch in separate terminals:

```bash
flux get kustomizations -A --watch
flux get helmreleases -A --watch
kubectl get pods -A --watch
kubectl get pvc -A
kubectl get replicationdestinations,replicationsources -A
```

For a waiting restore, inspect snapshots and events:

```bash
kubectl get volumesnapshots -A
kubectl get events -A --sort-by=.lastTimestamp
```

Pending PVCs are expected while VolSync downloads data and publishes restore snapshots.
Do not remove `dataSourceRef`, initialize an empty backup repository or bypass startup
guards to make an application Ready. For older known-good recovery points, follow
[application recovery](rke2-operations.md#application-recovery) before allowing startup.

Confirm existing LDAP users and group membership over TLS, the Minecraft world, and
historical VictoriaMetrics/VictoriaLogs queries. Verify new writes and public endpoints.
Then run:

```bash
task ansible:verify
task volsync:replication-sync
```

If RKE2 is running but the Flux handoff failed, ordinary bootstrap intentionally does
nothing. Fix the reported issue and resume only the handoff:

```bash
task ansible:bootstrap-flux
```

On a surviving cluster after cancelled uninstall, verify application storage before
releasing the maintenance hold on its coordinating inventory server:

```bash
task volsync:resume NODE=nas
```

Do not apply that hold journal to a new cluster. Fresh bootstrap removes it and
reconstructs normal schedules from Git. Writes after the final completed backup are
outside that recovery point.

## 5. Renew Hermes access

Once Flux has recreated Hermes ServiceAccounts, renew credentials in the existing
container:

```bash
task hermes:deploy-keys HOST=satellite
```

For a single profile:

```bash
task hermes:deploy-keys HOST=satellite -- --profile observability --duration-seconds 604800
```

Record the expiry printed by the command. Renewal does not revoke still-valid old
tokens; see [Hermes access](hermes-cluster-access.md) for compromise response and
container prerequisites.

## Validation scope

Application-level S3 restores and the uninstall backup hold/resume sequence have been
verified independently. The complete destructive cluster rebuild remains an operator
run: it has not been performed by these script checks. Current inventory and ZFS
placement describe one node; this procedure does not establish multi-node storage
redundancy.
