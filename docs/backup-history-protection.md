<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
SPDX-License-Identifier: 0BSD
-->

# Protecting backup history on Hetzner

Restic retention and immutable history solve different problems. A successful
backup can contain a bad migration, and the cluster's writer must not be able to
permanently remove every older recovery point. Repository integrity checks detect
storage corruption; restored application contents establish usability.

## Independent recovery material

Keep these outside the cluster and independently of the main workstation:

- Hetzner account recovery access and an owner S3 credential that is never deployed.
- The bucket location, repository paths and restic password for backed-up apps.
- A recovery copy of the age identity needed for encrypted bootstrap configuration.
- Instructions for obtaining the old application image/configuration paired with a checkpoint.

A second copy protected by the same lost identity is insufficient. Prove access from
an independent machine: list an existing repository, restore an older checkpoint
into isolated storage, and check known application contents. Do not paste credentials
into issues or logs. Existing SOPS policies and encrypted secrets are user maintained;
adding a recipient or replacing deployed credentials requires the user's changes.

## Keep an owner credential separate before restricting the writer

[Hetzner credentials](https://docs.hetzner.com/storage/object-storage/faq/s3-credentials/)
are project-wide by default. A second key in the same project alone does not restrict
the cluster key. Use explicit bucket-policy denies for the deployed writer, or grant
a writer from a separate project only the required repository permissions. Keep an
owner key in the bucket's project to manage the policy and recover protected versions.

Before applying a policy, verify the independent owner key can read the target
bucket's versioning and policy. Save the existing policy and versioning response
using that owner identity. Preserve ordinary `DeleteObject` for restic lock removal
and pruning; block permanent version deletion and protection changes by the writer.

The following policy fragment illustrates the required writer deny. Replace the
bucket and principal identifiers, merge with the existing policy, and validate using
the independent owner before deploying it. Do not use `NotAction`, which Hetzner
[does not enforce](https://docs.hetzner.com/storage/object-storage/supported-actions/).

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "PreserveHistoryFromClusterWriter",
    "Effect": "Deny",
    "Principal": {"AWS": "arn:aws:iam:::user/pPROJECT_ID:WRITER_ACCESS_KEY"},
    "Action": [
      "s3:DeleteObjectVersion", "s3:PutBucketVersioning",
      "s3:PutLifecycleConfiguration", "s3:PutBucketPolicy", "s3:DeleteBucketPolicy",
      "s3:PutBucketObjectLockConfiguration", "s3:PutObjectRetention",
      "s3:PutObjectLegalHold", "s3:BypassGovernanceRetention", "s3:DeleteBucket"
    ],
    "Resource": ["arn:aws:s3:::BUCKET", "arn:aws:s3:::BUCKET/*"]
  }]
}
```

This is a policy fragment, not an applied or validated policy for the existing bucket.
Verify supported actions against Hetzner in a disposable bucket with the same access
model before restricting the production writer. No deployed key should be able to
change its own restrictions. Keep owner credentials outside Kubernetes.

## Versioned recovery and retention

Enable bucket versioning using the independently held owner profile:

```bash
aws --profile backup-owner --endpoint-url https://hel1.your-objectstorage.com \
  s3api put-bucket-versioning --bucket "$BACKUP_BUCKET" \
  --versioning-configuration Status=Enabled
```

[Versioning](https://docs.hetzner.com/storage/object-storage/faq/buckets-objects/)
retains overwritten objects and turns ordinary deletion into a delete marker.
It protects against the cluster writer only when that writer cannot delete versions,
suspend versioning, change lifecycle rules, or remove its policy. It starts protecting
subsequent operations; it cannot recover objects already permanently deleted.

Do not apply a lifecycle expiry to current restic objects: snapshots share data packs.
Use restic's existing retention/pruning for the live repository. If owner-managed
noncurrent-version expiry is introduced, choose its recovery window and storage budget
explicitly and prove that an older repository can be reconstructed before enabling it.
Restoring one snapshot manifest is insufficient if its packs or indexes were deleted.
Recover a consistent repository namespace to separate storage before native checks.

Hetzner [Object Lock](https://docs.hetzner.com/storage/object-storage/howto-protect-objects/protect-object-lock-retention/)
must be enabled when creating a bucket. It cannot be added to an existing unlocked
bucket. A protected second repository therefore requires a new bucket, an independent
copy process and access model, and validation of restic lock/prune behavior. Compliance
retention cannot be shortened after application; prepare and test that design before
committing to it. An in-cluster copy job with unrestricted destination credentials does
not provide independent protection.

## Acceptance before calling history protected

Use a disposable repository and keys with the same permissions as production:

1. Back up known application contents, change them, then create another checkpoint.
2. Prune/overwrite using the writer. Verify permanent version deletion and bucket
   protection changes are denied, while ordinary backups and lock cleanup still work.
3. Reconstruct the older repository in separate storage with the owner credentials.
   Run `restic check --read-data` and restore its old application image and data.
4. Check application contents and access. Repeat without the current cluster or
   workstation before treating the independent recovery path as proven.

The shared [VolSync contract](../kubernetes/components/volsync/README.md) describes
in-cluster freshness, integrity checks and controlled restores. Independent access
and history protection remain tracked in [#128](https://github.com/soupglasses/ops/issues/128)
and [#129](https://github.com/soupglasses/ops/issues/129). Jellyfin configuration and
media intentionally have no backups and are outside these recovery procedures.
