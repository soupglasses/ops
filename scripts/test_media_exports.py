# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT

"""Check the actual export template's NAS-only anonymous writer boundary."""

from pathlib import Path
import unittest

from jinja2 import Environment, StrictUndefined


TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / 'ansible/roles/media_storage/templates/media.exports.j2'
)


class MediaExportsTests(unittest.TestCase):
    def test_only_nas_root_squash_receives_the_media_writer_group(self):
        clients = ['172.21.71.10/32', '172.21.69.0/24', '172.21.70.0/24']
        rendered = Environment(undefined=StrictUndefined).from_string(
            TEMPLATE.read_text()
        ).render(
            media_storage_mountpoint='/srv/media',
            media_storage_nfs_clients=clients,
            media_storage_nfs_anonymous_writer_clients=['172.21.71.10/32'],
            media_storage_gid=2000,
            media_storage_nfs_fsid=71,
        )
        export = next(line for line in rendered.splitlines() if line.startswith('/srv/media'))
        fields = export.split()
        self.assertEqual(fields[0], '/srv/media')
        self.assertEqual(len(fields), len(clients) + 1)
        for client, field in zip(clients, fields[1:], strict=True):
            network, raw_options = field.split('(', 1)
            options = set(raw_options.rstrip(')').split(','))
            self.assertEqual(network, client)
            self.assertIn('root_squash', options)
            self.assertNotIn('no_root_squash', options)
            self.assertIn('sync', options)
            self.assertIn('mountpoint', options)
            if client == '172.21.71.10/32':
                self.assertIn('anonuid=65534', options)
                self.assertIn('anongid=2000', options)
            else:
                self.assertFalse(any(option.startswith('anon') for option in options))


if __name__ == '__main__':
    unittest.main()
