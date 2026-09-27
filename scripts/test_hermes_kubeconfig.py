#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT

"""Exercise credential installation through subprocesses without live credentials."""

import importlib.util
import json
import os
import pathlib
import subprocess
import tempfile
import sys

spec = importlib.util.spec_from_file_location(
    "rotate", pathlib.Path(__file__).with_name("hermes-kubeconfig.py")
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    profile = root / "profiles" / "opslead"
    profile.mkdir(parents=True)
    target = profile / "kubeconfig.yaml"
    package = root / "hermes_cli"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "config.py").write_text("""import json
import os

saved = {}

def read_raw_config():
    home = os.environ['HERMES_HOME']
    default = ['ROOT_SETTING'] if home == '/opt/data' else ['PROFILE_SETTING']
    return {'terminal': {'env_passthrough': list(saved.get(home, default))}}

def save_env_value(name, value):
    home = os.environ['HERMES_HOME']
    assert name in ('KUBECONFIG', 'HERMES_KUBECONFIG')
    expected = '/dev/null' if home == '/opt/data' else home + '/kubeconfig.yaml'
    assert value == expected
    if os.environ['CASE'] == 'setter-failure':
        raise RuntimeError('fixture')
    if os.environ['CASE'] == 'silent-env-noop':
        return
    os.environ[name] = value

def set_config_value(key, value):
    home = os.environ['HERMES_HOME']
    existing = 'ROOT_SETTING' if home == '/opt/data' else 'PROFILE_SETTING'
    assert key == 'terminal.env_passthrough'
    assert json.loads(value) == [existing, 'KUBECONFIG', 'HERMES_KUBECONFIG']
    if os.environ['CASE'] != 'silent-config-noop':
        saved[home] = json.loads(value)
""")
    fake = root / "mise"
    fake.write_text("""#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
assert 'fixture-token' not in ' '.join(args)
with open(args[args.index('--kubeconfig') + 1]) as config:
    assert json.load(config)['users'][0]['user']['token'] == 'fixture-token'
mode = os.environ['CASE']
if mode == 'auth-failure':
    sys.exit(1)
if 'whoami' in args:
    identity = 'system:serviceaccount:hermes-system:opslead'
    if mode == 'wrong-identity':
        identity = 'wrong'
    print(json.dumps({'status': {'userInfo': {'username': identity}}}))
elif mode == 'read-denied':
    sys.exit(1)
""")
    fake.chmod(0o755)
    source = (
        mod.INSTALL.replace("'/opt/data/profiles/'", repr(str(root / "profiles") + "/"))
        .replace("'/usr/bin/mise'", repr(str(fake)))
        .replace("cwd='/opt/data'", "cwd=" + repr(tmp))
    )
    envelope = {
        "profile": "opslead",
        "identity": "system:serviceaccount:hermes-system:opslead",
        "expiry": "2026-10-27T00:00:00Z",
        "config": {"users": [{"user": {"token": "fixture-token"}}]},
    }
    for case in [
        "auth-failure",
        "wrong-identity",
        "read-denied",
        "setter-failure",
        "silent-env-noop",
        "silent-config-noop",
        "success",
    ]:
        target.write_text("previous-config")
        result = subprocess.run(
            [sys.executable, "-c", source, "1.34.2"],
            input=json.dumps(envelope),
            text=True,
            capture_output=True,
            env={**os.environ, "CASE": case, "PYTHONPATH": tmp},
        )
        assert "fixture-token" not in result.stdout + result.stderr
        assert not list(profile.glob(".kubeconfig-*"))
        if case == "success":
            assert result.returncode == 0, result.stderr
            assert json.loads(target.read_text()) == envelope["config"]
            assert target.stat().st_mode & 511 == 384
        else:
            assert result.returncode != 0
            assert target.read_text() == "previous-config"
        print(case, "passed")
