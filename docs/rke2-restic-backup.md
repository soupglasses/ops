<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# Encrypted etcd recovery points

`scripts/rke2-restic-backup.py` creates a fresh local RKE2 snapshot with native S3
upload explicitly disabled, then encrypts it with restic before uploading to the
existing off-cluster application backup endpoint under `rke2-etcd-restic`.
The host's `rke2-etcd-restic-backup.timer` runs the same helper every six hours
(00:05, 06:05, 12:05 and 18:05 UTC, plus up to two minutes of jitter). It catches up
after downtime and runs ten minutes after boot. Manual, scheduled and pre-upgrade
backups share a host lock. The upgrade requires a verified backup before cordon.

Flux creates `flux-system/rke2-etcd-restic` from existing cluster substitutions.
No credential values belong in this runbook, shell arguments, or backup receipts.
The first run requires explicit repository initialization:

```bash
mise exec -- python3 scripts/rke2-restic-backup.py --initialize
```

Subsequent runs omit `--initialize`. A missing or inaccessible repository is an
error, never a reason to initialize automatically. The installed helper uses the host's existing Kubernetes configuration. Manual runs
need verified SSH access to nas and passwordless sudo. It defaults to the human `sofie`
account; host, user and identity file can be overridden with its CLI options.

The root-only original is deleted after a successful encrypted restore. Failed runs
preserve their source for diagnosis and retry; no old recovery points are deleted.
Transfer goes directly from the node into a
restricted pod's memory-backed volume; no plaintext snapshot is saved to the operator's
workspace. Only the restic container receives backup credentials. A separate native
validator receives neither credentials nor a Kubernetes service-account token.
The temporary pod is removed on success or failure. Terminal or expired pods left
by a forced interruption are cleaned up by the next serialized run.

Verification checks the transfer SHA-256, rejection of an incorrect restic password,
`restic check --read-data`, restoration of the exact new snapshot ID, matching restored
SHA-256, and `etcdutl snapshot status` with nonzero revision and key count. This proves
retrieval, integrity and native database readability; it does not prove a replacement
control plane boots. The JSON receipt identifies the restic ID,
checksums, source RKE2 version and native validation results.

## Recovery without a surviving cluster

Keep an independent copy of this repository, its SOPS recovery identity, the original
RKE2 server token, restic password, and S3 endpoint/access credentials. The inventory's
`rke2_token` is encrypted in `ansible/inventories/home/group_vars/all/secrets.sops.yaml`;
a repository copy without the recovery identity is insufficient. Token equality with
the running server must be established by the operator before disaster recovery.

Install tools before Kubernetes or Flux, using the standalone playbook:

```bash
cd ansible
ansible-playbook playbooks/install-rke2-recovery-tools.yml
```

It installs restic and the S3 SDK from signed Ubuntu packages, and etcdutl 3.6.14
from the official release archive with pinned SHA-256
`ffe840ff9295808e88cce2794a18a5ac87f12a5203c8314d0bf6aa119b41bac5`.
The utility is `/opt/etcd-recovery/etcd-v3.6.14-linux-amd64/etcdutl`.
This playbook needs only SSH/sudo and package/artifact access: it neither starts RKE2
nor requires a Kubernetes Secret. Cache its packages/archive off-cluster for recovery
without internet access. Use the utility matching the selected backup's etcd version.

On a trusted recovery machine, supply `RESTIC_REPOSITORY`, `RESTIC_PASSWORD`,
`AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` through protected environment or
credential files. These are the same existing backup credentials used by Flux.
The repository is the existing endpoint plus `/rke2-etcd-restic`. Then:

```bash
restic snapshots --tag rke2-etcd
restic check --read-data
restic restore SNAPSHOT_ID --target /protected/recovery
sha256sum /protected/recovery/work/source/snapshot.db
/opt/etcd-recovery/etcd-v3.6.14-linux-amd64/etcdutl snapshot status \
  /protected/recovery/work/source/snapshot.db --write-out=json
```

Compare the checksum and versions with restored `work/source/recovery.json` and the
recorded receipt. Select an older known-good ID if corruption predates the latest
backup. Install the recorded RKE2 release, supply the original server token, stop its
service, and follow the [RKE2 recovery procedure](https://docs.rke2.io/datastore/backup_restore)
using the restored local file with `--etcd-s3=false` and
`--cluster-reset --cluster-reset-restore-path=/protected/recovery/work/source/snapshot.db`.
Preserve existing data first; never reset a healthy live cluster as a verification step.
See the [existing recovery runbook](rke2-s3-etcd-restore.md) for host and service ordering;
its native S3 download instructions apply only to historical native backups.

The helper pins restic 0.18.1 through the VolSync image and etcdutl 3.6.14 through the
official etcd image; their full digests are recorded in `recovery.json`. It refuses a
running etcd version mismatch until the validator is reviewed and updated. Recovery
can use standalone binaries from [restic releases](https://github.com/restic/restic/releases/tag/v0.18.1)
and [etcd releases](https://github.com/etcd-io/etcd/releases/tag/v3.6.14), so it does not
require these container registries or a working Kubernetes cluster. Preserve release
artifacts and checksums alongside the independent recovery material.

## Schedule, monitoring and retention

`ansible/playbooks/encrypt-etcd-backups.yml` installs and tests the service before
cutover, disables RKE2's built-in S3 uploader and automatic snapshots through a
non-secret configuration drop-in, restarts the same RKE2 version, and proves explicit
snapshot creation still works. It then enables the timer and retires native-S3
freshness metrics. Ordinary host convergence declares the same final configuration.
A clean cluster must restore Flux and its existing backup Secret before the timer can
succeed; recovery itself uses standalone restic and does not require the lost cluster.

```bash
cd ansible
ansible-playbook playbooks/encrypt-etcd-backups.yml
```

The service has a 30-minute timeout; its pod has a shorter lifetime. Root-only
`/var/lib/rke2-restic-backup/status.json` contains sanitized receipts. Atomic node
exporter metrics record attempts, outcome, repository count, and the last fully
verified encrypted restore. Alerts cover missing metrics, failure, a verified backup
older than ten hours, and fewer than 28 recovery points for eight days. Grafana shows
these encrypted-backup metrics. Failed attempts never refresh the verified timestamp.

Every encrypted recovery point is retained: the helper never forgets or prunes.
This preserves at least the former 28-point history as the repository fills; review
storage growth before introducing deletion. Backend lifecycle rules must preserve
restic objects, and independent versioned history must survive writer compromise.
Historical native S3 points must be encrypted, restored and validated individually
before their plaintext originals can be deleted. New backups use only restic uploads.

## Verified recovery point — 2026-09-27

The first live run completed at `2026-09-27T11:00:20Z` on RKE2 `v1.35.8+rke2r1`:

- Restic snapshot: `35c8df15efb350b31bd5a74d508ecab5873b2f0183661c44cbf32c380deb5655`.
- Source and restored SHA-256:
  `ecb9d9cd25f5249b7f7e1851a92124439993a097295ecde6d87405d616e6b3fc`.
- Native validation: revision `2184007`, `2214` keys, `48631808` bytes,
  database hash `3053146087`; checked with etcdutl `3.6.14`.
- Local plaintext original was deleted after successful encrypted restore verification
  at the operator's request; absence was checked over SSH.

The incorrect-password test, full repository data check and exact-ID restore all
passed. The helper exited successfully and deleted its temporary pod. Security and
resilience reviews covered the implementation and recovery documentation; the restore
evidence validates retrieval and native readability, not a control-plane replacement.

After plaintext cleanup, Gitleaks 8.30.1 scanned the two backup commits
(`cbdedf9..66548ac`) and reported no leaks. The encrypted etcd backup necessarily
contains Kubernetes Secrets when decrypted; this check concerns exposed secrets in
Git, not removal of required recovery data from the encrypted backup.
