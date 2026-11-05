#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT
"""Extract validation schemas from rendered local CRDs and Crossplane XRDs."""

import json
from pathlib import Path
import re
import sys


def write_schemas(resources, destination):
    count = 0
    for resource in resources:
        if not isinstance(resource, dict) or resource.get("kind") not in {
            "CustomResourceDefinition", "CompositeResourceDefinition"}:
            continue
        specification = resource["spec"]
        group = specification["group"]
        kind = specification["names"]["kind"].lower()
        if not re.fullmatch(r"[a-z0-9.-]+", group) or not re.fullmatch(r"[a-z0-9]+", kind):
            raise ValueError("unexpected schema group or kind")
        for version in specification["versions"]:
            name = version["name"]
            if not re.fullmatch(r"[a-z0-9]+", name):
                raise ValueError("unexpected schema version")
            schema = version["schema"]["openAPIV3Schema"]
            output = destination / group / (kind + "_" + name + ".json")
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(schema) + "\n")
            count += 1
    if not count:
        raise ValueError("no local CRD schemas were rendered")


if __name__ == "__main__":
    write_schemas(json.load(sys.stdin), Path(sys.argv[1]))
