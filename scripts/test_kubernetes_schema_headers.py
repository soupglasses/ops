# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
#
# SPDX-License-Identifier: 0BSD

"""Verify schema synchronization through the operator's command-line interface."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("kubernetes-schema-headers.py").resolve()


class SchemaHeaderTests(unittest.TestCase):
    def test_inventory_upgrade_requires_matching_editor_schemas(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = root / "ansible/inventories/home/group_vars/all/main.yml"
            inventory.parent.mkdir(parents=True)
            inventory.write_text("rke2_version: v1.36.4+rke2r1\n")
            manifest = root / "kubernetes/app.yaml"
            manifest.parent.mkdir()
            original = (
                "# yaml-language-server: $schema=https://raw.githubusercontent.com/"
                "yannh/kubernetes-json-schema/master/v1.36.4-standalone-strict/service-v1.json\n"
                "apiVersion: v1\nkind: Service\n"
            )
            manifest.write_text(original)
            subprocess.run(["git", "init", "--quiet", directory], check=True)
            subprocess.run(["git", "add", "ansible", "kubernetes"], cwd=root, check=True)

            def run(*args):
                return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=root,
                                      text=True, capture_output=True)

            self.assertEqual(run().returncode, 0)
            inventory.write_text("rke2_version: v1.37.0+rke2r2\n")
            failed = run()
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("kubernetes/app.yaml: expected v1.37.0", failed.stdout)
            self.assertEqual(manifest.read_text(), original)
            self.assertEqual(run("--write").returncode, 0)
            self.assertEqual(manifest.read_text(), original.replace("v1.36.4", "v1.37.0"))
            self.assertEqual(run().returncode, 0)
            self.assertEqual(run("--write").returncode, 0)


if __name__ == "__main__":
    unittest.main()
