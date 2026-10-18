# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT

import importlib.util
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

RUNTIME = Path(__file__).parents[1] / "runtime.py"
SPEC = importlib.util.spec_from_file_location("runtime", RUNTIME)
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


class PluginTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "config/server"
        (self.source / "plugins/configurations").mkdir(parents=True)
        (self.source / "plugins/configurations/ldap.xml").write_text("<LDAP />")

    def test_image_owned_plugins_replace_derived_binaries_and_preserve_configuration(
        self,
    ):
        distribution = self.root / "image-plugins"
        (distribution / "LDAP-Auth_23").mkdir(parents=True)
        (distribution / "LDAP-Auth_23/LDAP.dll").write_bytes(b"pinned plugin")
        (self.source / "plugins/LDAP-Auth_22").mkdir()
        (self.source / "plugins/LDAP-Auth_22/LDAP.dll").write_bytes(b"old plugin")
        (self.source / "plugins/Other_1").mkdir()
        (self.source / "plugins/SSO-Auth_4.0.0.4").mkdir()
        runtime.seed_plugins(self.source, distribution)
        self.assertFalse((self.source / "plugins/SSO-Auth_4.0.0.4").exists())
        self.assertFalse((self.source / "plugins/LDAP-Auth_22").exists())
        self.assertEqual(
            (self.source / "plugins/LDAP-Auth_23/LDAP.dll").read_bytes(),
            b"pinned plugin",
        )
        self.assertEqual(
            (self.source / "plugins/configurations/ldap.xml").read_text(), "<LDAP />"
        )
        self.assertTrue((self.source / "plugins/Other_1").is_dir())
        fresh = self.root / "fresh/config/server"
        runtime.seed_plugins(fresh, distribution)
        self.assertEqual(
            (fresh / "plugins/LDAP-Auth_23/LDAP.dll").read_bytes(), b"pinned plugin"
        )


    def test_launcher_hands_pid_and_signals_to_application(self):
        marker = self.root / "pid"
        child = "import os,time; from pathlib import Path; Path(" + repr(str(marker)) + ").write_text(str(os.getpid())); time.sleep(30)"
        process = subprocess.Popen([sys.executable, str(RUNTIME), sys.executable, "-c", child],
                                   env={**os.environ, "JELLYFIN_DATA_DIR": str(self.source)})
        self.addCleanup(lambda: process.poll() is None and process.kill())
        for _ in range(100):
            if marker.exists():
                break
            time.sleep(0.02)
        self.assertTrue(marker.exists(), "application did not start")
        self.assertEqual(int(marker.read_text()), process.pid)
        process.terminate()
        self.assertEqual(process.wait(timeout=5), -signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()
