<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# OpenLDAP volsync migration runbook

`openldap-data` lives on `openebs-hostpath`: node-local, on the EPHEMERAL partition,
cannot be CSI-snapshotted, and has no backup. A `talos:rebootstrap` destroys every
human account and password. Target state: a volsync-managed PVC `openldap` on
`openebs-zfs`, backed up daily to restic repo `volsync-openldap`, with a portable
`slapcat` LDIF dump written into the volume before each snapshot.

Facts this plan depends on (verified against the live pod):

- slapd runs as uid/gid 65534 (nobody); mdb files are mode 0600, so the volsync mover
  runs as 65534 (`VOLSYNC_PUID`/`VOLSYNC_PGID` in `ks.yaml`).
- `slapcat -f /config/slapd.conf` works in the image and dumps the full tree.
- The image has bash, which the quiesce watcher needs for `kubectl exec`.
- The data is under 1 MiB; capacity 5Gi is generous headroom.

## Phase 0: wiring (committed, safe)

The volsync + volsync-quiesce components are added to the openldap app. This is safe
to deploy while openldap still runs on the old PVC: it only creates the new PVC
`openldap` (populated empty via the bootstrap seed), the ReplicationSource/Destination
pair, the restic secret, and the quiesce watcher. The deployment still mounts
`openldap-data`. The daily ReplicationSource run backs up the empty new PVC until
switchover; harmless. The quiesce watcher will exec a daily `slapcat` dump into the
live volume; also harmless (and a free extra safety net).

## Phase 1: roll out and verify wiring

Push, `task flux:reconcile`, then confirm:

```bash
kubectl -n identity get kustomization openldap        # Ready
kubectl -n identity get pvc openldap                  # Bound (empty, seeded)
kubectl -n identity get job openldap-volsync-bootstrap  # Completed
kubectl -n identity get replicationsource openldap
```

## Phase 2: safety dump (before touching anything)

```bash
kubectl exec -n identity deploy/openldap -- \
  slapcat -f /config/slapd.conf > .private/openldap-$(date +%F).ldif
grep -c '^dn:' .private/openldap-*.ldif   # note the entry count
```

## Phase 3: copy data old -> new

Identity auth (canaille logins) is down from here until phase 4 completes.

```bash
flux suspend kustomization openldap -n identity
kubectl scale -n identity deploy/openldap --replicas=0
kubectl wait -n identity --for=delete pod -l app.kubernetes.io/name=openldap --timeout=120s
```

Copy with a throwaway pod (must run as root to preserve uid 65534 ownership):

```bash
kubectl apply -n identity -f - <<'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: openldap-data-copy
spec:
  restartPolicy: Never
  containers:
    - name: copy
      image: docker.io/library/alpine:3.22
      command: [sh, -c, "cp -a /old/. /new/ && ls -ln /new && echo COPY-OK"]
      volumeMounts:
        - {name: old, mountPath: /old, readOnly: true}
        - {name: new, mountPath: /new}
  volumes:
    - {name: old, persistentVolumeClaim: {claimName: openldap-data}}
    - {name: new, persistentVolumeClaim: {claimName: openldap}}
EOF
kubectl logs -n identity -f pod/openldap-data-copy   # expect COPY-OK, files uid 65534
kubectl delete -n identity pod/openldap-data-copy
```

`kubectl pv-migrate` (installed via mise) is the alternative if the ad-hoc pod is
blocked by pod security.

## Phase 4: switchover commit

In `kubernetes/apps/identity/openldap/app/deployment.yaml` change the data volume:

```yaml
        - name: data
          persistentVolumeClaim:
            claimName: openldap   # was: openldap-data
```

Keep `pvc.yaml` (the old PVC) in the kustomization for now; it is the on-node safety
copy. Push, then:

```bash
flux resume kustomization openldap -n identity
flux reconcile kustomization openldap -n identity --with-source
kubectl -n identity rollout status deploy/openldap
kubectl exec -n identity deploy/openldap -- slapcat -f /config/slapd.conf | grep -c '^dn:'
# entry count must match phase 2; then verify a canaille login works
```

## Phase 5: first real backup

```bash
kubectl -n identity patch replicationsource openldap --type merge \
  -p '{"spec":{"trigger":{"manual":"post-migration"}}}'
kubectl -n identity get replicationsource openldap \
  -o jsonpath='{.status.lastSyncTime} {.status.latestMoverStatus.result}{"\n"}'   # Successful
flux reconcile kustomization openldap -n identity   # restores the schedule trigger
```

## Phase 6: cleanup commit (only after phase 5 says Successful)

Delete `app/pvc.yaml` and its entry in `app/kustomization.yaml`. Flux prune removes
the old `openldap-data` PVC and its hostpath data. From this point the restic repo is
the recovery path, same as minecraft.

## Rollback (any time before phase 6)

Scale to 0, revert the deployment volume to `claimName: openldap-data`, resume and
reconcile. The old PVC is untouched until phase 6.
