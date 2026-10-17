# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT
"""Maintain one application ACL without replacing unrelated ordered rules."""

import argparse
import base64
import fcntl
import re
import subprocess
import sys
import time
from pathlib import Path

ALLOWED_ATTRIBUTES = {
    "entry",
    "uid",
    "cn",
    "displayname",
    "givenname",
    "sn",
    "mail",
    "memberof",
    "jpegphoto",
}


class ReconcileError(Exception):
    pass


def run(arguments, data=None):
    result = subprocess.run(arguments, input=data, capture_output=True)
    if result.returncode:
        # Offline tools may report configuration values; never forward their output.
        raise ReconcileError("LDAP configuration operation failed")
    return result.stdout


def parse_ldif(data):
    unfolded = []
    for line in data.decode().splitlines():
        if line.startswith(" "):
            if not unfolded:
                raise ReconcileError("invalid folded LDIF")
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)
    entries, entry = [], {}
    for line in unfolded + [""]:
        if not line:
            if entry:
                entries.append(entry)
                entry = {}
            continue
        if line.startswith("#") or line == "-":
            continue
        key, separator, value = line.partition(":")
        if not separator:
            raise ReconcileError("invalid LDIF field")
        if value.startswith(":"):
            value = base64.b64decode(value[1:].lstrip(), validate=True).decode()
        elif value.startswith("<"):
            raise ReconcileError("LDIF URL values are unsupported")
        else:
            value = value.lstrip(" ")
        entry.setdefault(key.casefold(), []).append(value)
    return entries


def field(name, value):
    # Base64 handles newlines and leading/trailing whitespace without custom escaping.
    return name + ":: " + base64.b64encode(value.encode()).decode() + "\n"


def unordered(value):
    return re.sub(r"^\{\d+\}", "", value)


def normalized(value):
    value = " ".join(unordered(value).casefold().split()).replace(
        "dn.exact=", "dn.base="
    )
    return re.sub(
        r"attrs=([^ ]+)",
        lambda match: "attrs=" + ",".join(sorted(match[1].split(","))),
        value,
    )


def subjects(value):
    return re.findall(
        r'\bby\s+dn(?:\.exact|\.base)?="([^"]+)"', unordered(value), re.IGNORECASE
    )


def belongs(value, owner):
    canonical = normalized(value)
    return (
        owner.casefold() in [subject.casefold() for subject in subjects(value)]
        and canonical.startswith('to dn.subtree="')
        and ' filter="' in canonical
        and canonical.endswith('" read by * break')
    )


def plan(existing, desired, owner, placement="after-password"):
    """Return delete/add values, preserving every rule outside this identity's ownership."""
    owned = [value for value in existing if belongs(value, owner)]
    if len(owned) > len(desired):
        raise ReconcileError("ambiguous managed ACL ownership")
    legacy = [value for value in existing if not belongs(value, owner)]
    if placement == "after-password":
        guard = re.search(r"\battrs=([^ ]+)", normalized(legacy[0])) if legacy else None
        if not guard or "userpassword" not in guard[1].split(","):
            raise ReconcileError("first legacy ACL must protect user passwords")
        index = 1
    else:
        anchors = [
            index
            for index, value in enumerate(legacy)
            if normalized(value).startswith("to * ")
        ]
        if not anchors:
            raise ReconcileError("legacy catch-all ACL anchor is missing")
        index = anchors[0]
    expected = legacy[:index] + desired + legacy[index:]
    if [normalized(value) for value in existing] == [
        normalized(value) for value in expected
    ]:
        return [], []
    return owned, [
        "{" + str(index + offset) + "}" + unordered(value)
        for offset, value in enumerate(desired)
    ]


def modify(dn, deleted, added):
    result = field("dn", dn) + "changetype: modify\n"
    if deleted:
        result += (
            "delete: olcAccess\n"
            + "".join(field("olcAccess", value) for value in deleted)
            + "-\n"
        )
    if added:
        result += "add: olcAccess\n" + "".join(
            field("olcAccess", value) for value in added
        )
    return (result + "\n").encode()


def policy(path):
    entries = parse_ldif(path.read_bytes())
    if len(entries) != 1 or set(entries[0]) != {"dn", "olcaccess"}:
        raise ReconcileError("policy must contain one DN and olcAccess value")
    entry = entries[0]
    if len(entry["dn"]) != 1 or len(entry["olcaccess"]) != 1:
        raise ReconcileError("policy must contain one DN and olcAccess value")
    dn, rule = entry["dn"][0], entry["olcaccess"][0]
    match = re.fullmatch(
        r'to dn\.subtree="[^"]+" filter="\(memberOf=[^()]+\)" attrs=([A-Za-z0-9,]+) by dn\.(?:exact|base)="([^"]+)" read by \* break',
        unordered(rule),
        re.IGNORECASE,
    )
    if not match or not set(match[1].casefold().split(",")).issubset(
        ALLOWED_ATTRIBUTES
    ):
        raise ReconcileError(
            "policy must grant only selected group-member profile attributes"
        )
    owners = [match[2]]
    return dn, rule, owners[0]


def bootstrap(config_directory, policy_path, timeout=120):
    database, desired, owner = policy(policy_path)
    daemon_lock = config_directory.parent / ".slapd.lock"
    configuration_lock = config_directory.parent / ".configuration.lock"
    with daemon_lock.open("a+") as daemon, configuration_lock.open(
        "a+"
    ) as configuration:
        deadline = time.monotonic() + timeout
        for stream in (daemon, configuration):
            while True:
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ReconcileError(
                            "directory is still in use; refusing an online configuration edit"
                        )
                    time.sleep(0.1)
        observed = parse_ldif(run(["slapcat", "-F", str(config_directory), "-n", "0"]))
        entries = [entry for entry in observed if entry.get("dn") == [database]]
        if len(entries) != 1:
            raise ReconcileError("policy database is missing or ambiguous")
        previous = entries[0].get("olcaccess", [])
        deleted, added = plan(previous, [desired], owner)
        if added:
            run(
                ["slapmodify", "-F", str(config_directory), "-n", "0"],
                modify(database, deleted, added),
            )
        checked = parse_ldif(run(["slapcat", "-F", str(config_directory), "-n", "0"]))
        entry = next(item for item in checked if item.get("dn") == [database])
        current = entry.get("olcaccess", [])
        if plan(current, [desired], owner) != ([], []):
            raise ReconcileError("offline ACL policy did not converge")
        if [normalized(value) for value in previous if not belongs(value, owner)] != [
            normalized(value) for value in current if not belongs(value, owner)
        ]:
            raise ReconcileError("unmanaged ACL verification failed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=Path("/policy/jellyfin.ldif"))
    parser.add_argument("--config-directory", type=Path, default=Path("/data/config"))
    parser.add_argument("--lock-timeout", type=float, default=120)
    args = parser.parse_args()
    try:
        bootstrap(args.config_directory, args.policy, args.lock_timeout)
        print("Offline LDAP ACL policy verified", flush=True)
        return 0
    except (ReconcileError, ValueError, UnicodeError, OSError):
        print(
            "Offline LDAP ACL policy failed; refusing directory startup",
            file=sys.stderr,
            flush=True,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
