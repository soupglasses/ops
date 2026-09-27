#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
#
# SPDX-License-Identifier: 0BSD

"""Render a restricted runtime for a separately prepared, migrated example PVC."""

import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
NAME = "openldap-nfrastack-example"
IMAGE = "docker.io/nfrastack/openldap:2.6-8.1.1@sha256:5e5c8fdd1d7e3a9623500839699b258d0569bf965528b85c682ec5d0902f5d6c"
metadata = {"name": NAME, "namespace": "identity", "labels": {"app": NAME}}
config = {"reload-tls.sh": (HERE / "reload-tls.sh").read_text()}
security = {
    "runAsNonRoot": True, "runAsUser": 389, "runAsGroup": 389,
    "allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
    "capabilities": {"drop": ["ALL"]},
}
mounts = [
    {"name": "data", "mountPath": "/data"},
    {"name": "data", "mountPath": "/var/lib/openldap", "subPath": "db"},
    {"name": "tls", "mountPath": "/certs", "readOnly": True},
    {"name": "run", "mountPath": "/run/openldap"},
]
pod = {
    "apiVersion": "v1", "kind": "Pod", "metadata": metadata,
    "spec": {
        "automountServiceAccountToken": False,
        "securityContext": {"fsGroup": 389, "seccompProfile": {"type": "RuntimeDefault"}},
        "hostAliases": [{"ip": "127.0.0.1", "hostnames": ["ldap.finnes.dev"]}],
        "initContainers": [{
            "name": "require-restored-directory", "image": IMAGE,
            "command": ["/bin/sh", "-ec"],
            "args": ["test -s /data/db/data.mdb; test -s /data/config/cn=config.ldif; "
                     "test -s '/data/config/cn=config/olcDatabase={1}mdb.ldif'; "
                     "mkdir -p /run/openldap/run"],
            "securityContext": security,
            "volumeMounts": [{"name": "data", "mountPath": "/data"},
                             {"name": "run", "mountPath": "/run/openldap"}],
        }],
        "containers": [{
            "name": "openldap", "image": IMAGE,
            "command": ["/usr/sbin/slapd"],
            "args": ["-F", "/data/config", "-h", "ldaps://0.0.0.0:1636/ ldapi:///", "-d", "256"],
            "securityContext": security,
            "volumeMounts": mounts,
            "ports": [{"name": "ldaps", "containerPort": 1636}],
            "readinessProbe": {
                "exec": {"command": ["/bin/sh", "-ec", "openssl s_client -connect 127.0.0.1:1636 "
                                     "-servername ldap.finnes.dev -verify_hostname ldap.finnes.dev "
                                     "-verify_return_error </dev/null >/dev/null 2>&1"]},
                "timeoutSeconds": 5, "periodSeconds": 10,
            },
            "resources": {"requests": {"cpu": "100m", "memory": "128Mi"},
                          "limits": {"memory": "512Mi"}},
        }, {
            "name": "tls-reload", "image": IMAGE,
            "command": ["/bin/sh", "/source/reload-tls.sh"],
            "securityContext": security,
            "volumeMounts": [
                {"name": "source", "mountPath": "/source", "readOnly": True},
                {"name": "tls", "mountPath": "/certs", "readOnly": True},
                {"name": "run", "mountPath": "/run/openldap"},
            ],
            "resources": {"requests": {"cpu": "5m", "memory": "16Mi"},
                          "limits": {"memory": "64Mi"}},
        }],
        # This claim must be prepared and restore-tested before applying the example.
        # No empty-volume initialization is allowed in the normal runtime.
        "volumes": [
            {"name": "data", "persistentVolumeClaim": {"claimName": NAME + "-data"}},
            {"name": "run", "emptyDir": {}},
            {"name": "source", "configMap": {"name": NAME}},
            {"name": "tls", "secret": {"secretName": "openldap-tls", "defaultMode": 0o440}},
        ],
    },
}
policy = {
    "apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy", "metadata": metadata,
    "spec": {"podSelector": {"matchLabels": {"app": NAME}}, "policyTypes": ["Ingress", "Egress"],
             "ingress": [], "egress": []},
}
print(json.dumps({"apiVersion": "v1", "kind": "List", "items": [
    {"apiVersion": "v1", "kind": "ConfigMap", "metadata": metadata, "data": config},
    policy, pod,
    {"apiVersion": "v1", "kind": "Service", "metadata": metadata,
     "spec": {"selector": {"app": NAME},
              "ports": [{"name": "ldaps", "port": 636, "targetPort": "ldaps"}]}},
]}, indent=2))
