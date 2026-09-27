<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# Encrypted etcd recovery points

`scripts/rke2-restic-backup.py` creates a fresh local RKE2 snapshot with native S3
upload explicitly disabled, then encrypts it with restic before uploading to the
existing off-cluster application backup endpoint under `rke2-etcd-restic`.
The upgrade playbook requires this backup and its restore verification before cordon.

Flux creates `flux-system/rke2-etcd-restic` from existing cluster substitutions.
No credential values belong in this runbook, shell arguments, or backup receipts.
The first run requires explicit repository initialization:

```bash
mise exec -- python3 scripts/rke2-restic-backup.py --initialize
```

Subsequent runs omit `--initialize`. A missing or inaccessible repository is an
error, never a reason to initialize automatically. The helper needs working kubectl,
verified SSH access to nas and passwordless sudo. It defaults to the human `sofie`
account; host, user and identity file can be overridden with its CLI options.

The root-only original remains on nas. Transfer goes directly from SSH into a
restricted pod's memory-backed volume; no plaintext snapshot is saved to the operator's
workspace. Only the restic container receives backup credentials. A separate native
validator receives neither credentials nor a Kubernetes service-account token.
The temporary pod is removed on success or failure.

Verification checks the transfer SHA-256, rejection of an incorrect restic password,
`restic check --read-data`, restoration of the exact new snapshot ID, matching restored
SHA-256, and `etcdutl snapshot status` with nonzero revision and key count. This proves
retrieval, integrity and native database readability; it does not prove a replacement
control plane boots. The JSON receipt identifies the retained local file, restic ID,
checksums, source RKE2 version and native validation results.

## Recovery without a surviving cluster

Keep an independent copy of this repository, its SOPS recovery identity, the original
RKE2 server token, restic password, and S3 endpoint/access credentials. The inventory's
`rke2_token` is encrypted in `ansible/inventories/home/group_vars/all/secrets.sops.yaml`;
a repository copy without the recovery identity is insufficient. Token equality with
the running server must be established by the operator before disaster recovery.

On a trusted recovery machine, supply `RESTIC_REPOSITORY`, `RESTIC_PASSWORD`,
`AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` through protected environment or
credential files. These are the same existing backup credentials used by Flux.
The repository is the existing endpoint plus `/rke2-etcd-restic`. Then:

```bash
restic snapshots --tag rke2-etcd
restic check --read-data
restic restore SNAPSHOT_ID --target /protected/recovery
sha256sum /protected/recovery/work/source/snapshot.db
etcdutl snapshot status /protected/recovery/work/source/snapshot.db --write-out=json
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

## Retention and remaining work

This repository retains every manually created/pre-upgrade recovery point; the helper
never runs forget or prune. Backend lifecycle rules must retain its objects, and
independent versioned history must survive writer credential compromise.
Failures exit nonzero and block the upgrade. This is an on-demand backup, without a
separate scheduled freshness alert.

Existing six-hourly native RKE2 S3 backups and their alerts remain unchanged. Those
uploads are not protected by restic client-side encryption. Moving their schedule,
retention and freshness monitoring to encrypted backups remains a separate change;
retain historical recovery points throughout that migration.
