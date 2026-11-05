# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT

"""Install image-owned plugins, then hand process lifecycle directly to Jellyfin."""

import os
from pathlib import Path
import shutil
import sys
import uuid


def seed_plugins(source, distribution=Path("/opt/jellyfin/plugins")):
    if not distribution.exists():
        return
    plugins = source / "plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    # Retire only the former image-owned SSO binary; retain saved configuration.
    retired = plugins / "SSO-Auth_4.0.0.4"
    if retired.exists() or retired.is_symlink():
        if not retired.is_dir() or retired.is_symlink():
            raise RuntimeError("unexpected retired image-managed plugin path")
        shutil.rmtree(retired)
    for plugin in distribution.iterdir():
        prefix = next(
            (name for name in ("LDAP-Auth_",) if plugin.name.startswith(name)),
            None,
        )
        if prefix is None or not plugin.is_dir() or plugin.is_symlink():
            raise RuntimeError("unexpected image-managed plugin distribution")
        temporary = plugins / (".seed-" + uuid.uuid4().hex)
        shutil.copytree(plugin, temporary)
        target = plugins / plugin.name
        if target.is_symlink():
            raise RuntimeError("image-managed plugin target cannot be a symlink")
        if target.exists():
            shutil.rmtree(target)
        os.rename(temporary, target)
        for previous in plugins.glob(prefix + "*"):
            if previous != target:
                if not previous.is_dir() or previous.is_symlink():
                    raise RuntimeError("unexpected image-managed plugin path")
                shutil.rmtree(previous)


if __name__ == "__main__":
    seed_plugins(Path(os.environ.get("JELLYFIN_DATA_DIR", "/config/server")))
    os.execv(sys.argv[1], sys.argv[1:])
