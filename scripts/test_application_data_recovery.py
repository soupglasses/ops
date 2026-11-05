# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT
"""Opt-in data rollback drills with real applications and an isolated restic repo.

These prove data rollback by restoring the pre-upgrade checkpoint with its
original image. They do not exercise CSI, S3 or arbitrary version downgrades. Images come from the declarations used by the cluster.
"""

import contextlib
import datetime
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]


def run(*args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=120, **kwargs).stdout


@unittest.skipUnless(os.getenv("APPLICATION_RECOVERY_TEST") == "1", "set APPLICATION_RECOVERY_TEST=1")
class ApplicationRecovery(unittest.TestCase):
    def exercise(self, application):
        manifest = f"kubernetes/apps/monitoring/{application}/app/helmrelease.yaml"
        release = json.loads(run("yq", "-o=json", ".", str(ROOT / manifest)))
        previous = run("git", "show", os.getenv("RECOVERY_BASE_REF", "origin/main") + ":" + manifest, cwd=ROOT)
        previous_release = json.loads(run("yq", "-o=json", ".", input=previous))
        metrics = application == "victoria-metrics"
        def declared_image(document):
            config = document["spec"]["values"]["vmsingle"]["spec"]["image"] if metrics else document["spec"]["values"]["server"]["image"]
            return config["repository"] + ":" + config["tag"]

        image, previous_image = declared_image(release), declared_image(previous_release)
        for artifact in {image, previous_image}:
            run("docker", "pull", artifact)
        port = 8428 if metrics else 9428
        with tempfile.TemporaryDirectory(prefix="ops-recovery-") as directory, contextlib.ExitStack() as cleanup:
            root = Path(directory)
            data = root / "data"
            data.mkdir()
            env = {**os.environ, "RESTIC_REPOSITORY": str(root / "repository"), "RESTIC_PASSWORD": uuid.uuid4().hex}
            # Existing host restic configuration must not redirect this synthetic drill.
            for key in ("RESTIC_REPOSITORY_FILE", "RESTIC_PASSWORD_FILE", "RESTIC_PASSWORD_COMMAND"):
                env.pop(key, None)
            run("restic", "init", env=env)
            name = "ops-recovery-" + uuid.uuid4().hex[:12]
            cleanup.callback(subprocess.run, ["docker", "rm", "--force", name], capture_output=True)

            def start(path, artifact=image):
                run("docker", "run", "--detach", "--name", name, "--user", f"{os.getuid()}:{os.getgid()}",
                    "--cap-drop=ALL", "--security-opt=no-new-privileges", "--publish", f"127.0.0.1::{port}",
                    "--mount", f"type=bind,src={path},dst=/storage", artifact,
                    "-storageDataPath=/storage", "-retentionPeriod=1y", "-storage.minFreeDiskSpaceBytes=1MB")
                hostport = run("docker", "port", name, str(port)).strip().rsplit(":", 1)[1]
                base = "http://127.0.0.1:" + hostport
                deadline = time.monotonic() + 30
                while True:
                    try:
                        with urllib.request.urlopen(base + "/health", timeout=2):
                            return base
                    except (OSError, urllib.error.URLError):
                        if time.monotonic() > deadline:
                            self.fail("application did not become healthy: " + run("docker", "logs", name))
                        time.sleep(0.25)

            def stop():
                run("docker", "stop", "--time", "20", name)
                run("docker", "rm", name)

            now = datetime.datetime.now(datetime.timezone.utc)
            milliseconds = int(now.timestamp() * 1000)

            def request(base, path, fields=None, body=None):
                if fields:
                    path += "?" + urllib.parse.urlencode(fields)
                with urllib.request.urlopen(urllib.request.Request(base + path, data=body, headers={"Content-Type": "text/plain" if metrics else "application/json"}), timeout=10) as response:
                    return response.read().decode()

            def write(base, marker):
                if metrics:
                    body = f'ops_recovery_fixture{{checkpoint="{marker}"}} 42 {milliseconds}\n'.encode()
                    request(base, "/api/v1/import/prometheus", body=body)
                else:
                    body = (json.dumps({"_time": now.isoformat(), "_msg": marker, "drill": "ops-recovery"}) + "\n").encode()
                    request(base, "/insert/jsonline", body=body)
                request(base, "/internal/force_flush", body=b"")

            def contents(base):
                if metrics:
                    response = request(base, "/api/v1/export", {"match[]": "ops_recovery_fixture", "start": milliseconds / 1000 - 60, "end": milliseconds / 1000 + 60})
                    return {json.loads(line)["metric"]["checkpoint"] for line in response.splitlines()}
                response = request(base, "/select/logsql/query", {"query": 'drill:"ops-recovery"'})
                return {json.loads(line)["_msg"] for line in response.splitlines()}

            def expect_contents(base, expected):
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    actual = contents(base)
                    if actual == expected:
                        return
                    time.sleep(0.25)
                self.assertEqual(actual, expected, run("docker", "logs", name))

            def checkpoint():
                output = run("restic", "backup", ".", "--json", cwd=data, env=env)
                return next(json.loads(line)["snapshot_id"] for line in output.splitlines() if json.loads(line).get("message_type") == "summary")

            base = start(data, previous_image)
            write(base, "before-change")
            expect_contents(base, {"before-change"})
            stop()
            before = checkpoint()
            base = start(data)
            write(base, "after-change")
            expect_contents(base, {"before-change", "after-change"})
            stop()
            after = checkpoint()
            run("restic", "check", "--read-data", env=env)
            for snapshot, artifact, expected in [(before, previous_image, {"before-change"}), (after, image, {"before-change", "after-change"})]:
                restored = root / snapshot
                restored.mkdir()
                run("restic", "restore", snapshot, "--target", str(restored), env=env)
                base = start(restored, artifact)
                expect_contents(base, expected)
                stop()
            # Corruption must be observable, not just a successful check of a good repo.
            pack = next(path for path in (root / "repository/data").rglob("*") if path.is_file())
            pack.chmod(0o600)
            with pack.open("r+b") as damaged:
                first = damaged.read(1)
                damaged.seek(0)
                damaged.write(bytes([first[0] ^ 1]))
            result = subprocess.run(["restic", "check", "--read-data"], env=env, capture_output=True, timeout=120)
            self.assertNotEqual(result.returncode, 0, "corrupted repository passed its integrity check")

    def test_metrics_older_checkpoint_undoes_later_writes(self):
        self.exercise("victoria-metrics")

    def test_logs_older_checkpoint_undoes_later_writes(self):
        self.exercise("victoria-logs")


if __name__ == "__main__":
    unittest.main()
