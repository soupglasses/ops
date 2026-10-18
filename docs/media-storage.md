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


## Backup policy

Jellyfin configuration and the shared media library have no VolSync/restic backups,
by operator choice. Existing live PVCs and the NAS dataset are preserved. A fresh
configuration PVC starts an empty Jellyfin installation; lost media must be obtained
again. Other applications retain their existing backup policies.

On a replacement OS with surviving disks, import the existing pool through the normal
host role and remount the preserved dataset. The NFS CSI StorageClass uses a fixed
`library` subdirectory and `onDelete: retain`. Recreating the media PVC rebinds that
directory. Local snapshots, if present, do not survive NAS or pool loss.

See [Jellyfin backup cleanup](jellyfin-backup-cleanup.md) for the one-time removal of
old controllers and backup history after deploying this policy.
