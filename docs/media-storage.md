<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: MIT
-->

# Media storage and recovery

Media lives in the dedicated ZFS dataset `zfspool/media`, mounted at `/srv/media` on
`nas`. It is separate from Kubernetes PVC datasets and Jellyfin's configuration and
database. The library directories are `library/movies`, `library/tv` and `library/music`.

## Host configuration

After the normal host setup has imported the existing pool, converge the media role:

```sh
cd ansible
ansible-playbook playbooks/media-storage.yml --check --diff
ansible-playbook playbooks/media-storage.yml
```

The role creates only this child dataset. It refuses a conflicting mountpoint or host
files that mounting the dataset would hide. It never recreates a pool or destroys a
dataset. Compression is `zstd`, record size is `1M`, access-time updates are disabled,
and POSIX ACLs use ZFS system attributes. Compression helps compressible content;
already compressed video generally saves little additional space. Existing datasets,
PVCs and media files retain their contents and ownership.

The dataset quota is `1T`, matching the Kubernetes media PVC's `1Ti` request.
It includes local snapshot usage and keeps media from consuming the rest of the
pool. Change the Ansible quota and PVC capacity together when expanding it;
do not remove the bound and let uploads exhaust application storage.

`media-storage-mount.service` mounts this dataset after the existing non-mounting pool
import service and before NFS/RKE2. The NFS service also requires that unit. The export
uses `mountpoint`, so a missing dataset cannot silently export the underlying OS
directory. [OpenZFS properties](https://openzfs.github.io/openzfs-docs/man/v2.3/7/zfsprops.7.html)
and [NFS export options](https://manpages.ubuntu.com/manpages/resolute/man5/exports.5.html)
describe these settings.

## Linux and application access

The server exports `172.21.71.10:/srv/media` over NFSv4 TCP 2049, with synchronous writes
and root squashing. Allowed clients are the NAS node and the trusted management LANs
listed in `ansible/roles/media_storage/defaults/main.yml`. The Flux-managed host
firewall must permit that port from the same private clients; public access remains
denied. NFSv2, NFSv3 and UDP are disabled. NFS AUTH_SYS trusts the allowed clients'
numeric user/group IDs; this is private LAN storage, not an Internet-facing share.

The NAS export client (`172.21.71.10/32`) additionally maps squashed root to UID
`65534` and media writer GID `2000`. Ubuntu's NFS `manage-gids` replaces a root
CSI process's supplementary groups with the host's root groups, so the client's
supplementary media group alone does not provide access. This mapping grants the
mounting plugins shared-writer access while preserving root squashing and the
private `2770` directory mode. It does not grant root ownership or administrator
access. LAN and VPN export clients retain the default anonymous identity.

The shared writer group is `media`, GID `2000`. The role adds the named administrator
to it. New login sessions pick up the membership. Directories use setgid and default
ACLs, so new files retain shared group access without making the share world readable.
Application pods need supplementary group `2000`; keep their configuration-volume
UID/GID settings independent. Jellyfin mounts media read-write so it can save artwork, NFO metadata and subtitles beside media. Nextcloud can mount the
same NFS share in its pod and expose that mount through its Local external-storage
backend; it does not need its own copy of the library.

Example Linux mount:

```sh
sudo mkdir -p /mnt/media
sudo mount -t nfs -o vers=4.2,hard,nosuid,nodev \
  172.21.71.10:/srv/media/library /mnt/media
```

Use a client user in group `2000` for writes. A client root user is squashed and does
not gain administrator access. Import media into `movies`, `tv` or `music`, write
uploads under temporary names, then atomically rename completed files into place.
Avoid modifying published files during backup. If a full point-in-time view is needed,
stop every writer before the backup; a live file-level backup cannot promise a single
instant across independently modified files.

## Independent backups

Jellyfin's configuration checkpoint does **not** contain media. The media PVC needs a
separate off-cluster restic repository and scheduled Direct backup because NFS has no
CSI snapshot support. Use the existing off-cluster endpoint and independently retained
recovery credentials, with application backup failure/age monitoring and sufficient
capacity for the entire library and retention history. Local ZFS snapshots do not
survive NAS or pool loss.

`media_restore.py` protects clean-cluster recovery. It preserves an existing dataset
only after checking its persistent `.ops-media-manifest.json` and canary hash. It
refuses unknown populated directories rather than overwrite them. For an empty dataset,
it selects one restic snapshot, records its full ID, restores with `--verify`, and
checks the canary. Retries use that same snapshot even if newer history appears.
Suspend the media ReplicationSource during an operator-initiated restore and resume
it only after validation, so partial recovery cannot become a new backup.
An exclusive NFS file lock prevents overlapping restore jobs. External Linux and
Nextcloud writers must remain stopped until recovery completes.

A genuinely new empty library requires both first-install flags:

```sh
python3 /usr/local/lib/jellyfin/media_restore.py --root /media \
  --allow-initialize --initialize-repository
```

Only the predefined empty library directories are ignored when determining emptiness.
Repository initialization is explicit; authentication, network or repository-read
failures cannot be treated as an empty library. After initialization, take an
off-cluster backup, restore it in isolation and remove both flags from Git. The hidden
canary and manifest must remain in every backup. Exclude `.ops-media-restore*` control
files; they describe an in-progress recovery rather than library contents.

## Recovery checks

On a replacement OS with surviving disks, import the existing pool through the normal
host role and remount the preserved dataset. Rebuilding Kubernetes must not replay an
older backup over surviving media. After actual pool loss, create the empty media
dataset, restore it from the off-cluster repository through the guarded restore job,
then enable Jellyfin and other writers. The NFS CSI StorageClass uses a fixed
`library` subdirectory and `onDelete: retain`, while Kubernetes PV objects use Delete
reclamation. Recreating a PVC therefore preserves and rebinds the same directory
without retaining a stale claim UID. The driver leaves ownership to the host ACLs
with `fsGroupPolicy: None`; a cluster admission policy reserves this shared storage
class for `media/jellyfin-media`.

For delayed corruption, preserve the current dataset and select an older known-good
snapshot into an isolated recovery directory or dataset. Compare and validate files
before replacing any current media. The automatic guard intentionally cannot overwrite
an initialized live library; recovering selected older content is an operator action.

A restore drill must verify the canary and representative real-file hashes and prove
that Jellyfin can read the restored library. A first-install empty-library drill proves
the backup/recovery wiring and canary only; repeat with real media after importing it.
Keep a rollback copy during migrations and delete temporary drill resources afterward.
Recovery requires the repository, off-cluster endpoint, restic password, S3 access
credentials, host administrator access and the normal Flux recovery prerequisites.

### Initial off-cluster restore evidence

On 2026-10-17, the initial media backup was restored from the off-cluster repository
into an isolated `emptyDir` by a restricted, non-root Job. It mounted no NAS or live
media PVC. Runtime image digest
`54ed2633b05a399f14f35fc3b23285dbf25b02b4f8ae3dbeb411866ce8444820`
ran the normal recovery helper without either initialization flag.

The restore completed at 17:17:16 UTC with restic content verification and recovery
manifest validation:

- Snapshot: `e42a5ba16304ac248feda473320c57d49572a4e574c8d519739ac7cdf97a0192`.
- Canary SHA-256: `762c19c80ead377ef8c5253e2616a3b5ccb100904ef55080252a0dbe8bb8e4c2`.
- Two regular files: the persistent canary and its manifest.

The temporary Job and pod were deleted after validation. This proves initial
off-cluster recovery and the expected snapshot layout; the empty library contained no
real media. Repeat the drill with representative media hashes after importing files.
