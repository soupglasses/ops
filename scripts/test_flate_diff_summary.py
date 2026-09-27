# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
#
# SPDX-License-Identifier: 0BSD

"""Exercise the comment generator through its CI command-line interface."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("flate-diff-summary.py")
UNIFIED = """--- Deployment apps/web
+++ Deployment apps/web
@@ -1,3 +1,3 @@
 kind: Deployment
-old: value
+new: value
 unchanged: value
@@ -8 +8,2 @@
-old: second
+new: second
+extra: value

--- ConfigMap apps/obsolete
+++ ConfigMap apps/obsolete
@@ -1,2 +0,0 @@
-kind: ConfigMap
-data: {}

--- ConfigMap apps/added
+++ ConfigMap apps/added
@@ -0,0 +1,2 @@
+kind: ConfigMap
+data: {}
"""


class SummaryTests(unittest.TestCase):
    def render(self, readable, unified=UNIFIED):
        with tempfile.TemporaryDirectory() as directory:
            readable_path = Path(directory) / "diff.txt"
            unified_path = Path(directory) / "diff-unified.txt"
            readable_path.write_text(readable)
            unified_path.write_text(unified)
            return subprocess.check_output(
                [sys.executable, str(SCRIPT), str(readable_path), str(unified_path),
                 "https://example.com/artifact"], text=True,
            )

    def test_small_diff_has_overview_and_full_diff(self):
        body = self.render("+new: value\n-old: value\n")
        self.assertIn("3 rendered resources, 5 lines added, 4 lines removed", body)
        self.assertIn("| `Deployment apps/web` | 3 | 2 |", body)
        self.assertIn("| `ConfigMap apps/obsolete` | 0 | 2 |", body)
        self.assertIn("| `ConfigMap apps/added` | 2 | 0 |", body)
        self.assertIn("```diff\n+new: value\n-old: value\n```", body)

    def test_oversized_diff_retains_overview_and_link(self):
        body = self.render("+" + "é" * 30_000)
        self.assertIn("3 rendered resources, 5 lines added, 4 lines removed", body)
        self.assertIn("exceeds the inline size limit", body)
        self.assertIn("https://example.com/artifact", body)
        self.assertNotIn("```diff", body)
        self.assertLessEqual(len(body.encode()), 60_000)

    def test_large_resource_table_is_bounded(self):
        unified = "".join(
            f"--- ConfigMap apps/{index}\n+++ ConfigMap apps/{index}\n@@ -0,0 +1 @@\n+data: {{}}\n"
            for index in range(3_000)
        )
        body = self.render("+" * 60_000, unified)
        self.assertIn("3,000 rendered resources, 3,000 lines added, 0 lines removed", body)
        self.assertIn("more resources in the complete diff", body)
        self.assertLessEqual(len(body.encode()), 60_000)

    def test_empty_diff(self):
        self.assertEqual(self.render("", ""), "No rendered Flux diff.\n")


if __name__ == "__main__":
    unittest.main()
