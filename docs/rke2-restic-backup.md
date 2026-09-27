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
it restores a local database obtained through restic, with native S3 access disabled.

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

## Scheduled cutover — 2026-09-27

The host service passed restore validation before and after a same-version RKE2
restart. The timer is enabled, the old native metric timer is disabled, and the
non-secret drop-in disables both native S3 upload and native automatic snapshots.
The immediate timer run restored snapshot
`69c693a5c71799217a13fdb9d389f610043d68755e62faf527ee116d223719a5` at
`2026-09-27T11:24:48Z`: source/restored SHA-256
`99a6fd2b38e328adbfaa0e8209604e4fb67dd1e91e07df84e0dfde770320a7b3`,
revision `2194034`, `1925` keys. Its local plaintext file and temporary pod were
removed. VictoriaMetrics ingested `rke2_etcd_restic_backup_success=1`.
The node remained Ready on `v1.35.8+rke2r1`; Cilium rollout completed.

## Migrating historical native S3 recovery points

`scripts/rke2-encrypt-history.py` is a one-time root host tool. It consumes the existing
RKE2 base configuration internally; retain those old S3 settings until the migration
finishes. The overriding drop-in keeps native uploads disabled throughout migration.
Its default `--migrate` stage preserves all source objects. A separate `--delete`
stage requires the unchanged source inventory and a restored encrypted coverage index,
then revalidates every object before deleting exact versions. Source changes or
unsupported enumeration/conditional deletion stop the operation.

The inventory covers snapshot objects, companion `.metadata` objects, historical
versions and delete markers. Incomplete multipart uploads block completion and require
separate inspection. Object bytes and metadata go into the encrypted restic repository;
only sanitized digests, restic IDs and validation results appear in the local receipt.
The source SDK consumes credentials without exporting them to files or log output.
Original and restored bytes are streamed through memory-backed storage, including
root-only `/run` storage for native validation. No plaintext is written to the
operator's workspace.

For historical recovery on a trusted machine, supply the same independent restic
credentials as above and run as root:

```bash
restic snapshots --tag rke2-etcd-history
restic restore SNAPSHOT_ID --target /protected/history
```

An object recovery point contains `work/source/original` and encrypted
`work/source/recovery.json`; the latter records its original key, version, timestamp,
checksum and object metadata. Coverage-index recovery points contain
`work/index/coverage.json`, mapping source digests to all verified object snapshots.
The index itself is also stored in restic, so local migration receipts are not needed
for disaster recovery. Select a database object rather than its metadata companion.
For a ZIP snapshot, validate and extract its single database member to a protected
path; keep the original archive and compare its SHA-256 with `recovery.json` first.
Validate the extracted database with the recorded native tool, then follow the local
RKE2 restore procedure with the original token and matching RKE2 release. Historical
objects retain their original bytes; they are not silently converted to the current
scheduled-backup layout.

## Historical migration evidence — 2026-09-27

All 27 native S3 database objects were encrypted and restored with matching SHA-256,
then validated using etcdutl 3.6.14. Native results ranged from 493 to 3,361 keys
and revisions 13,500 to 2,067,702. No separate companion objects were present; original
object metadata was preserved inside each encrypted recovery point. Full repository
data verification passed. The coverage index was backed up and read back successfully:

- Coverage restic ID:
  `5730c7da16efe7d384edd68f9c56b123cf4b8fc01d6cdc9affbb75d8aa584714`.
- Source inventory SHA-256:
  `cb9de1ab4bb87cf0a4a4d4b94103dbec4e956fa8f32566d7afdca628578a0c1a`.

After independent security and resilience approval, the deletion stage restored and
validated all 27 objects again, required unchanged source inventory, and deleted the
exact original versions. Final enumeration found no current objects, historical data
versions, delete markers or multipart uploads under the configured native etcd prefix.
Encrypted history and the coverage index remain retained. This proves API-visible
cleanup of the scoped native backup history, not erasure of a provider's internal copies.

After historical cleanup, the normal systemd service completed another encrypted
backup and exact restore at `2026-09-27T11:40:56Z`:
`b582260129c2042551fbe238f130d9120d4408082ed49cb19f37bdbb9ab3622b`,
SHA-256 `e5bb268d6780f5931239cd5307946ae58b9bf87743c020ec20fec9c627abd4f0`,
revision `2200197`, `1955` keys. Temporary pods, host tmpfs validation directories,
and newly generated local plaintext snapshots were absent afterward. The one-time
host migration executable was removed; its reviewed source remains in Git.
Standalone restic `0.18.1` and etcdutl `3.6.14` were verified on the host. Implementation
commits passed Gitleaks 8.30.1 without exposed secrets.

## Automated bootstrap validation

Use the [bootstrap guide](rke2-s3-etcd-restore.md) for the supported operator commands.
Normal backups now include each Bound PVC's actual OpenEBS ZFS dataset and GUID.
The collector checks identities before and after snapshot capture; the standalone
bootstrap restore requires those same datasets before permitting RKE2 startup.
Legacy snapshots without this evidence remain encrypted archives but are not accepted
by the automatic bootstrap. Storage loss uses fresh GitOps and VolSync recovery.

Validation on 2026-09-27 covered twelve restore-helper cases using real restic,
six volume-inventory cases, and a forced-Ansible-handler failure test. A separate
native drill created an etcd database, encrypted and restored it through the helper,
started a new etcd member from that restored database and read back the original key.
Temporary containers and plaintext fixtures were removed. Only ZFS identities were
mocked in that isolated native drill; real node dataset discovery and read-only ZFS
queries under the service's restricted capabilities were also checked independently.
A complete replacement-host bootstrap remains a separate drill, tracked in TODO.md.

The deployed job completed a fresh encrypted backup and native restore check at
2026-09-27 13:12 UTC: restic snapshot `7917b209`, matching source/restored SHA-256
`59a3ee55c0459678a5fb92419f2e9b7e12bd67b212ff44c107ac88c32839917d`,
revision 2,234,622 and 2,401 keys. The inventory collector successfully resolved all
ten Bound PVCs on the node, including retained migration and cache volumes. The
verification Pod and new plaintext snapshot were removed; the timer remained active.
The node and all non-suspended Flux Kustomizations were Ready after the reboot.

The Flux handoff's reconciliation task was exercised separately against the running
cluster without importing credentials or restarting controllers. It passed after
replacing annotation-acknowledgement waits with current-generation readiness and
matching Git/applied revisions: the root can remove its own request annotation while
successfully reconciling itself.
