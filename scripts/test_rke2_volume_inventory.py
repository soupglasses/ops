#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
#
# SPDX-License-Identifier: 0BSD

"""Exercise the storage identity evidence used to gate restored-etcd startup."""

import importlib.util
import json
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location(
    'backup', Path(__file__).with_name('rke2-restic-backup.py'))
BACKUP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BACKUP)


class VolumeInventoryTest(unittest.TestCase):
    def setUp(self):
        self.claim = {'metadata': {'namespace': 'identity', 'name': 'openldap-directory',
                                  'uid': 'claim-id'},
                      'spec': {'volumeName': 'pv-id'}, 'status': {'phase': 'Bound'}}
        self.pv = {'metadata': {'name': 'pv-id'}, 'spec': {
            'claimRef': {'namespace': 'identity', 'name': 'openldap-directory', 'uid': 'claim-id'},
            'csi': {'driver': 'zfs.csi.openebs.io', 'volumeHandle': 'pvc-dataset'}}}
        self.volume = {'metadata': {'name': 'pvc-dataset'}, 'spec': {
            'poolName': 'zfspool/k8s', 'volumeType': 'DATASET', 'ownerNodeID': 'nas'}}
        self.objects = {'persistentvolumeclaims': [self.claim], 'persistentvolumes': [self.pv],
                        'zfsvolumes.zfs.openebs.io': [self.volume]}
        self.guid = '12345678901234567890'

    def command(self, argv):
        if argv[:2] == ['kubectl', 'get']:
            return json.dumps({'items': self.objects[argv[2]]}).encode()
        self.assertEqual(argv, ['/usr/sbin/zfs', 'get', '-H', '-p', '-o', 'value',
                                'guid', 'zfspool/k8s/pvc-dataset'])
        if self.guid is None:
            raise RuntimeError('Dataset absent')
        return (self.guid + '\n').encode()

    def capture(self):
        return BACKUP.capture_volume_inventory(self.command, node='nas')

    def test_claim_maps_to_actual_dataset_guid(self):
        self.assertEqual(self.capture(), [{'namespace': 'identity', 'pvc': 'openldap-directory',
                                          'dataset': 'zfspool/k8s/pvc-dataset', 'guid': self.guid}])

    def test_missing_storage_is_never_omitted(self):
        for resource in ('persistentvolumes', 'zfsvolumes.zfs.openebs.io'):
            with self.subTest(resource=resource):
                original = self.objects[resource]
                self.objects[resource] = []
                with self.assertRaises(RuntimeError):
                    self.capture()
                self.objects[resource] = original
        self.guid = None
        with self.assertRaises(RuntimeError):
            self.capture()

    def test_unknown_driver_wrong_claim_and_foreign_node_fail(self):
        for target, key, value in ((self.pv['spec']['csi'], 'driver', 'other.csi'),
                                   (self.pv['spec']['claimRef'], 'uid', 'replaced-claim'),
                                   (self.volume['spec'], 'ownerNodeID', 'other-node'),
                                   (self.volume['spec'], 'volumeType', 'ZVOL'),
                                   (self.volume['spec'], 'poolName', '../unsafe')):
            with self.subTest(key=key):
                original = target[key]
                target[key] = value
                with self.assertRaises(RuntimeError):
                    self.capture()
                target[key] = original

    def test_deleting_duplicate_and_lost_claims_fail(self):
        self.claim['metadata']['deletionTimestamp'] = '2026-09-27T00:00:00Z'
        with self.assertRaises(RuntimeError):
            self.capture()
        del self.claim['metadata']['deletionTimestamp']
        self.objects['persistentvolumeclaims'].append(self.claim)
        with self.assertRaises(RuntimeError):
            self.capture()
        self.objects['persistentvolumeclaims'].pop()
        self.claim['status']['phase'] = 'Lost'
        with self.assertRaises(RuntimeError):
            self.capture()

    def test_unbound_pending_claim_is_not_claimed_as_preserved(self):
        self.objects['persistentvolumeclaims'].append({
            'metadata': {'namespace': 'default', 'name': 'pending'},
            'spec': {}, 'status': {'phase': 'Pending'}})
        self.assertEqual(len(self.capture()), 1)

    def test_invalid_guid_fails(self):
        for guid in ('', '0', '123\n456', 'not-a-guid', str(2 ** 64)):
            with self.subTest(guid=guid):
                self.guid = guid
                with self.assertRaises(RuntimeError):
                    self.capture()


if __name__ == '__main__':
    unittest.main()
