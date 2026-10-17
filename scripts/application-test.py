# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT

"""Run application-owned acceptance checks without changing desired configuration."""

import argparse
import json
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", required=True)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--deployment")
    target.add_argument("--release")
    args = parser.parse_args()
    if args.release:
        subprocess.run(["helm", "test", args.release, "--namespace", args.namespace,
                        "--logs", "--timeout", "5m"], check=True)
        return
    result = subprocess.run(["kubectl", "get", "deployment", args.deployment,
                             "--namespace", args.namespace, "-o", "json"],
                            capture_output=True, text=True, check=True)
    deployment = json.loads(result.stdout)
    annotations = deployment["metadata"].get("annotations", {})
    command = json.loads(annotations["testing.ops.finnes.dev/command"])
    container = annotations["testing.ops.finnes.dev/container"]
    if not isinstance(command, list) or not command or not all(isinstance(item, str) for item in command):
        raise ValueError("acceptance command must be a nonempty JSON string array")
    if container not in {item["name"] for item in deployment["spec"]["template"]["spec"]["containers"]}:
        raise ValueError("acceptance container does not exist")
    subprocess.run(["kubectl", "rollout", "status", "deployment/" + args.deployment,
                    "--namespace", args.namespace, "--timeout=120s"], check=True)
    subprocess.run(["kubectl", "exec", "deployment/" + args.deployment,
                    "--namespace", args.namespace, "--container", container, "--", *command], check=True)


if __name__ == "__main__":
    main()
