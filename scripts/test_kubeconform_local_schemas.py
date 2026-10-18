# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT
"""Validate real application CRs against the chart's generated API schemas."""

import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LocalSchemaTests(unittest.TestCase):
    def test_generated_jellyfin_schema_rejects_non_boolean_network_setting(self):
        resource = {"apiVersion": "jellyfin.ops.finnes.dev/v1alpha1",
                    "kind": "NetworkingConfiguration", "metadata": {"name": "jellyfin"},
                    "spec": {"managementPolicies": ["Observe"],
                             "providerConfigRef": {"kind": "ProviderConfig", "name": "jellyfin"},
                             "forProvider": {"enableRemoteAccess": True}}}
        valid = self.validate(resource)
        self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)
        resource["spec"]["forProvider"]["enableRemoteAccess"] = "enabled"
        invalid = self.validate(resource)
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("enableRemoteAccess", invalid.stdout + invalid.stderr)

    def test_plugin_settings_schema_requires_secret_key(self):
        resource = {"apiVersion": "jellyfin.ops.finnes.dev/v1alpha1",
                    "kind": "PluginSettings", "metadata": {"name": "ldap"},
                    "spec": {"providerConfigRef": {"name": "jellyfin"}, "pluginID": "plugin",
                             "configuration": {}, "secretValues": {"LdapBindPassword": {"name": "credentials", "key": "password"}}}}
        valid = self.validate(resource)
        self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)
        del resource["spec"]["secretValues"]["LdapBindPassword"]["key"]
        invalid = self.validate(resource)
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("key", invalid.stdout + invalid.stderr)

    def validate(self, resource):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "resource.yaml"
            path.write_text(json.dumps(resource))
            return subprocess.run([str(ROOT / "scripts/kubeconform.sh"), str(path)],
                                  cwd=ROOT, capture_output=True, text=True)

    def test_generated_cluster_provider_schema_rejects_invalid_port(self):
        resource = {"apiVersion": "ldap.ops.finnes.dev/v1beta1",
                    "kind": "ClusterProviderConfig", "metadata": {"name": "directory"},
                    "spec": {"host": "directory.example.test", "port": 636, "tls": True,
                             "bindDN": "cn=admin,dc=example,dc=test",
                             "credentialsSecretRef": {"namespace": "identity", "name": "directory", "key": "password"},
                             "allowedBases": ["ou=clients,dc=example,dc=test"]}}
        valid = self.validate(resource)
        self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)
        invalid = copy.deepcopy(resource)
        invalid["spec"]["port"] = "not-an-integer"
        result = self.validate(invalid)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("port", result.stdout + result.stderr)

    def test_composition_schema_rejects_unsupported_grant(self):
        resource = {"apiVersion": "identity.ops.finnes.dev/v1alpha1",
                    "kind": "OAuth2Client", "metadata": {"name": "application"},
                    "spec": {"backendRef": {"name": "directory"}, "clientID": "media.application",
                             "redirectURIs": ["https://application.example.test/callback"],
                             "tokenEndpointAuthMethod": "none", "grantTypes": ["authorization_code"]}}
        valid = self.validate(resource)
        self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)
        resource["spec"]["grantTypes"] = ["password"]
        invalid = self.validate(resource)
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("grantTypes", invalid.stdout + invalid.stderr)

    def test_group_of_names_schema_requires_valid_cn_without_human_members(self):
        resource = {"apiVersion": "identity.ops.finnes.dev/v1alpha1",
                    "kind": "GroupOfNames", "metadata": {"name": "application-users"},
                    "spec": {"backendRef": {"name": "directory-admin", "kind": "ProviderConfig"},
                             "cn": "application.users"}}
        valid = self.validate(resource)
        self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)
        invalid = copy.deepcopy(resource)
        invalid["spec"]["cn"] = "bad,ou=users"
        rejected = self.validate(invalid)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("cn", rejected.stdout + rejected.stderr)


if __name__ == "__main__":
    unittest.main()
