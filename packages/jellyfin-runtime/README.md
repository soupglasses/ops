<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: MIT
-->

# Jellyfin runtime and recovery

The image supervises Jellyfin and installs the pinned LDAP and SSO plugins. Application
configuration lives in `/config/server`; `/cache` is disposable. Media is a separate
read-write mount for media and sidecar metadata; it has its own independent backup.

## Offline checkpoints

The VolSync preparation command is:

```sh
python3 /usr/local/lib/jellyfin/runtime.py checkpoint
```

The supervisor gracefully stops Jellyfin and requires a successful exit. It checks every
SQLite database, hashes all configuration files, and writes a complete `checkpoint.tar`
to `/backup`. This includes plugin configuration and binaries, user images, artwork,
accounts and library metadata. It atomically replaces the previous archive only after
the new archive is complete and durable, then restarts Jellyfin. A failed export keeps
the previous archive and does not release the VolSync copy trigger.

VolSync snapshots the **staging PVC**, not the active configuration PVC. A delayed
snapshot can therefore contain a complete published archive and an incomplete temporary
archive, but recovery reads only `checkpoint.tar`. Jellyfin resumes before snapshotting
and upload; the quiesce component's resume command is a no-op. Its timeout cannot resume
database writes into the backed-up artifact. Do not point the source back at the live
configuration PVC.

Mount the staging PVC at `/backup` and a writable empty directory at `/run/jellyfin`.
The image entrypoint runs the supervisor. Use its control endpoint for liveness, which
remains available while Jellyfin is stopped for checkpoint creation:

```sh
python3 /usr/local/lib/jellyfin/runtime.py health --timeout 5
```

Use Jellyfin's `/health` endpoint for readiness. Configuration must contain regular
files and directories; symlinks cause checkpoints to fail rather than follow paths
outside the backed-up directory. Size staging for two complete archives because the
previous recovery point remains until publication. Monitor source failures and backup
age through the existing VolSync alerts.

## First installation and recovery

Run this init command before starting Jellyfin:

```sh
python3 /usr/local/lib/jellyfin/runtime.py restore --source /config
```

An existing `server` directory is validated and preserved. Otherwise the command
validates the restored archive's path names, file hashes and SQLite databases, then
promotes the extracted server directory atomically. Interrupted extraction can be
retried; an invalid archive cannot replace native state. Missing data fails startup.

Only a genuinely new application may use `--allow-initialize`. Complete its setup,
create the first off-cluster backup, restore and validate it in isolation, then remove
that flag from Git. Never enable it to work around missing recovery history.

The shared VolSync destination/populator restores the configuration PVC from the
staging snapshot. Use `task volsync:restore APP=jellyfin` or
`task volsync:restore-at APP=jellyfin AT=<timestamp>` for a newer or older recovery point;
the destination's `latestImage` is otherwise frozen. Preserve the original PVC or its
rollback snapshot until application verification succeeds.

Recovery needs this repository, the off-cluster S3 endpoint and repository, the restic
password and S3 access credentials supplied through cluster secrets, and the normal
cluster DNS, storage and Flux prerequisites. The staging PVC is reproducible from a
running restored application and is not itself the only recovery copy. Keep the image
digest, plugin locks, source references and Dockerfile so the runtime can be rebuilt
if its published image becomes unavailable. Restoring with an older Jellyfin version
after a database migration is unsafe; retain the matching image version with each
upgrade recovery point.

An isolated restore drill must check database integrity, expected user/library records,
server identity, LDAP and SSO configuration, plugin binaries and a known user image.
Start the restored server with the matching image and check its API. Record the backup
ID and non-sensitive verification results, then delete temporary resources. Media
requires its own backup and restore checks; local ZFS snapshots alone do not survive
loss of the NAS or pool.

## Tests

```sh
python3 -m unittest discover -s packages/jellyfin-runtime/tests -v
```

The tests exercise real child processes and the Unix control socket, checkpoint
failure preservation, corrupt or unsafe archives, first-install guards and restoration
of database records, plugin configuration and images. They need no cluster or secrets.
