# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT

import contextlib
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import urllib.parse
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import configuration


class ConfigurationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.state = {
            "completed": False,
            "libraries": [
                {
                    "Name": "Unmanaged",
                    "CollectionType": "homevideos",
                    "Locations": ["/media/personal"],
                }
            ],
            "password": None,
            "posts": [],
            "ldap": {
                "LdapServer": "wrong",
                "LdapBindPassword": "old",
                "LdapUsers": [
                    {
                        "LinkedJellyfinUserId": "one",
                        "ProfileImageHash": "retained",
                    }
                ],
            },
            "application": {
                "Settings": {
                    "managed": {
                        "Enabled": False,
                        "RuntimeLinks": {"alice": "one"},
                        "ApiKey": "old",
                    },
                    "other": {"Enabled": True},
                }
            },
        }
        state = self.state

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path == "/System/Info/Public":
                    result = {"StartupWizardCompleted": state["completed"]}
                elif self.path == "/Library/VirtualFolders":
                    result = state["libraries"]
                elif self.path == "/Startup/User":
                    result = {"Name": "root"}
                elif 'Token="fixture-token"' not in self.headers.get(
                    "Authorization", ""
                ):
                    self.send_error(401)
                    return
                else:
                    result = state[self.path[1:]]
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())

            def do_POST(self):
                data = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                body = json.loads(data) if data else None
                state["posts"].append(self.path)
                if self.path in state.get("fail_posts", []):
                    self.send_error(500)
                    return
                result = None
                if self.path == "/Startup/User":
                    state["password"] = body["Password"]
                elif self.path == "/Startup/Complete":
                    state["completed"] = True
                elif self.path == "/Users/AuthenticateByName":
                    if body != {
                        "Username": "ops-recovery",
                        "Pw": state["password"],
                    }:
                        self.send_error(401, "synthetic-credential-must-not-appear")
                        return
                    result = {"AccessToken": "fixture-token"}
                elif self.path.startswith("/Library/VirtualFolders?"):
                    query = urllib.parse.parse_qs(
                        urllib.parse.urlsplit(self.path).query
                    )
                    state["libraries"].append(
                        {
                            "Name": query["name"][0],
                            "CollectionType": query["collectionType"][0],
                            "Locations": [
                                item["Path"]
                                for item in body["LibraryOptions"]["PathInfos"]
                            ],
                        }
                    )
                elif self.path.startswith("/Library/VirtualFolders/Paths?"):
                    library = next(
                        item
                        for item in state["libraries"]
                        if item["Name"] == body["Name"]
                    )
                    library["Locations"].append(body["Path"])
                elif self.path in ("/ldap", "/application"):
                    if 'Token="fixture-token"' not in self.headers.get(
                        "Authorization", ""
                    ):
                        self.send_error(401)
                        return
                    state[self.path[1:]] = body
                self.send_response(200 if result else 204)
                self.end_headers()
                if result:
                    self.wfile.write(json.dumps(result).encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.config = {
            "baseURL": f"http://127.0.0.1:{self.server.server_port}",
            "adminUsername": "ops-recovery",
            "startup": {
                "configuration": {},
                "remoteAccess": {"EnableAutomaticPortMapping": False},
            },
            "resources": [
                {
                    "path": "/ldap",
                    "values": {
                        "LdapServer": "ldap.finnes.dev",
                        "EnableLdapProfileImageSync": True,
                    },
                    "secretValues": [
                        {
                            "path": ["LdapBindPassword"],
                            "file": "LDAP_BIND_PASSWORD",
                        }
                    ],
                },
                {
                    "path": "/application",
                    "values": {"Settings": {"managed": {"Enabled": True}}},
                    "secretValues": [
                        {
                            "path": ["Settings", "managed", "ApiKey"],
                            "file": "APPLICATION_API_KEY",
                        }
                    ],
                },
            ],
        }
        self.path = self.directory / "configuration.json"
        self.path.write_text(json.dumps(self.config))
        for name in (
            "LDAP_BIND_PASSWORD",
            "APPLICATION_API_KEY",
            "JELLYFIN_ADMIN_PASSWORD",
        ):
            (self.directory / name).write_text("synthetic-credential-must-not-appear")
        self.reconciler = configuration.Reconciler(self.path, self.directory)

    def test_bootstrap_drift_restart_rotation_and_runtime_data(self):
        self.reconciler.run_once()
        self.assertTrue(self.state["completed"])
        self.assertEqual(
            self.state["ldap"]["LdapUsers"][0]["ProfileImageHash"], "retained"
        )
        self.assertEqual(
            self.state["application"]["Settings"]["managed"]["RuntimeLinks"],
            {"alice": "one"},
        )
        self.assertTrue(self.state["application"]["Settings"]["other"]["Enabled"])
        first_posts = copy.deepcopy(self.state["posts"])
        self.reconciler.run_once()
        self.assertEqual(self.state["posts"][len(first_posts) :], [])
        self.state["ldap"]["LdapServer"] = "drift"
        (self.directory / "APPLICATION_API_KEY").write_text("rotated-synthetic")
        restarted = configuration.Reconciler(self.path, self.directory)
        restarted.run_once()
        self.assertEqual(self.state["ldap"]["LdapServer"], "ldap.finnes.dev")
        self.assertEqual(
            self.state["application"]["Settings"]["managed"]["ApiKey"],
            "rotated-synthetic",
        )
        self.assertEqual(self.state["posts"].count("/Startup/User"), 1)
        self.assertEqual(
            self.state["ldap"]["LdapUsers"][0]["ProfileImageHash"], "retained"
        )

    def test_existing_install_wrong_password_fails_without_reset_or_leak(self):
        self.state["completed"] = True
        self.state["password"] = "a-different-synthetic-password"
        with self.assertRaises(configuration.ConfigurationError) as error:
            self.reconciler.run_once()
        self.assertNotIn("synthetic", str(error.exception))
        self.assertNotIn("/Startup/User", self.state["posts"])
        self.assertEqual(self.state["ldap"]["LdapServer"], "wrong")

    def test_polling_logs_changes_but_keeps_idle_polls_silent_and_healthy(
        self,
    ):
        marker = self.directory / "ready"
        output = io.StringIO()
        polls = 0

        def next_poll(interval):
            nonlocal polls
            polls += 1
            self.assertTrue(marker.exists())
            self.assertEqual(interval, 60)
            if polls == 1:
                self.assertEqual(output.getvalue().count("configuration reconciled"), 1)
                self.state["posts"].clear()
                marker.unlink()
            elif polls == 2:
                self.assertEqual(self.state["posts"], [])
                self.assertEqual(output.getvalue().count("configuration reconciled"), 1)
                self.state["ldap"]["LdapServer"] = "drift"
            else:
                self.assertEqual(self.state["posts"], ["/ldap"])
                self.assertEqual(output.getvalue().count("configuration reconciled"), 2)
                summary = json.loads(output.getvalue().splitlines()[-1])
                self.assertEqual(summary["changed"], 1)
                self.assertEqual(summary["updated"], 1)
                self.assertEqual(summary["created"], 0)
                self.assertEqual(summary["path_added"], 0)
                self.assertEqual(summary["initialized"], 0)
                raise StopIteration

        with (
            patch("sys.argv", ["configuration", "--health-file", str(marker)]),
            patch("configuration.Reconciler", return_value=self.reconciler),
            patch("configuration.time.sleep", side_effect=next_poll),
            contextlib.redirect_stdout(output),
            self.assertRaises(StopIteration),
        ):
            configuration.main()

    def test_check_reports_in_sync_without_logging_a_change(self):
        self.reconciler.run_once()
        self.state["posts"].clear()
        output = io.StringIO()
        with (
            patch("sys.argv", ["configuration", "--check"]),
            patch("configuration.Reconciler", return_value=self.reconciler),
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(configuration.main(), 0)
        self.assertEqual(output.getvalue(), "Jellyfin configuration is in sync\n")
        self.assertEqual(self.state["posts"], [])

    def test_change_logs_identify_fields_and_library_operations_without_values(
        self,
    ):
        self.config["libraries"] = [
            {
                "name": "Movies",
                "collectionType": "movies",
                "paths": ["/media/movies"],
            }
        ]
        self.path.write_text(json.dumps(self.config))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.reconciler.run_once()
            self.reconciler.run_once()
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        updates = [event for event in events if event["action"] == "updated"]
        self.assertEqual(
            [event["resource"] for event in updates], ["/ldap", "/application"]
        )
        self.assertEqual(
            updates[0]["fields"],
            [
                ["LdapServer"],
                ["EnableLdapProfileImageSync"],
                ["LdapBindPassword"],
            ],
        )
        self.assertEqual(
            updates[1]["fields"],
            [
                ["Settings", "managed", "Enabled"],
                ["Settings", "managed", "ApiKey"],
            ],
        )
        self.assertEqual(events[-1]["action"], "created")
        self.assertEqual(events[-1]["library"], "Movies")
        self.assertEqual(events[-1]["paths"], ["/media/movies"])
        self.assertNotIn("synthetic", output.getvalue())
        self.assertNotIn("fixture-token", output.getvalue())
        self.assertNotIn("RuntimeLinks", output.getvalue())
        self.state["libraries"][-1]["Locations"] = []
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.reconciler.run_once()
        event = json.loads(output.getvalue())
        self.assertEqual(event["action"], "path_added")
        self.assertEqual(event["library"], "Movies")
        self.assertEqual(event["path"], "/media/movies")

    def test_successful_changes_are_logged_even_if_a_later_resource_fails(
        self,
    ):
        self.reconciler.run_once()
        self.state["ldap"]["LdapServer"] = "drift"
        self.config["libraries"] = [
            {
                "name": "Unmanaged",
                "collectionType": "movies",
                "paths": ["/media/movies"],
            }
        ]
        self.path.write_text(json.dumps(self.config))
        output = io.StringIO()
        with (
            contextlib.redirect_stdout(output),
            self.assertRaises(configuration.ConfigurationError),
        ):
            self.reconciler.run_once()
        event = json.loads(output.getvalue())
        self.assertEqual(event["resource"], "/ldap")
        self.assertEqual(event["fields"], [["LdapServer"]])

    def test_failed_update_is_not_logged_as_success(self):
        self.reconciler.run_once()
        self.state["ldap"]["LdapServer"] = "drift"
        self.state["fail_posts"] = ["/ldap"]
        output = io.StringIO()
        with (
            contextlib.redirect_stdout(output),
            self.assertRaisesRegex(
                configuration.ConfigurationError, "POST /ldap failed"
            ),
        ):
            self.reconciler.run_once()
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(self.reconciler.changes, [])

    def test_authentication_after_restart_does_not_log_a_configuration_change(self):
        self.reconciler.run_once()
        restarted = configuration.Reconciler(self.path, self.directory)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            restarted.run_once()
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(restarted.changes, [])

    def test_missing_credentials_does_not_write_settings(self):
        self.reconciler.run_once()
        self.state["posts"].clear()
        (self.directory / "LDAP_BIND_PASSWORD").unlink()
        with self.assertRaises(configuration.ConfigurationError):
            self.reconciler.run_once()
        self.assertEqual(self.state["posts"], [])

    def test_check_only_detects_drift_without_writing(self):
        self.reconciler.run_once()
        self.state["ldap"]["LdapServer"] = "drift"
        self.state["posts"].clear()
        with self.assertRaises(configuration.ConfigurationError):
            self.reconciler.run_once(check_only=True)
        self.assertEqual(self.state["posts"], [])
        self.assertEqual(self.state["ldap"]["LdapServer"], "drift")

    def test_libraries_are_additive_and_checked_without_deletion(self):
        self.config["libraries"] = [
            {
                "name": "Movies",
                "collectionType": "movies",
                "paths": ["/media/movies"],
            }
        ]
        self.path.write_text(json.dumps(self.config))
        self.reconciler.run_once()
        self.assertEqual(len(self.state["libraries"]), 2)
        self.assertEqual(self.state["libraries"][0]["Name"], "Unmanaged")
        self.state["posts"].clear()
        self.reconciler.run_once()
        self.assertEqual(self.state["posts"], [])
        self.state["libraries"][1]["Locations"] = ["/media/extra"]
        with self.assertRaises(configuration.ConfigurationError):
            self.reconciler.run_once(check_only=True)
        self.reconciler.run_once()
        self.assertEqual(
            self.state["libraries"][1]["Locations"],
            ["/media/extra", "/media/movies"],
        )
        self.state["libraries"][1]["CollectionType"] = "music"
        with self.assertRaises(configuration.ConfigurationError):
            self.reconciler.run_once()
        self.assertEqual(len(self.state["libraries"]), 2)

    def test_health_marker_staleness(self):
        marker = self.directory / "ready"
        with patch(
            "sys.argv",
            ["configuration", "--health", "--health-file", str(marker)],
        ):
            self.assertEqual(configuration.main(), 1)
            marker.touch()
            self.assertEqual(configuration.main(), 0)
            os.utime(marker, (1, 1))
            self.assertEqual(configuration.main(), 1)

    def test_refuses_external_api(self):
        self.config["baseURL"] = "https://jellyfin.example.test"
        self.path.write_text(json.dumps(self.config))
        with self.assertRaises(configuration.ConfigurationError):
            self.reconciler.run_once()
        self.assertEqual(self.state["posts"], [])

    def test_response_credentials_never_printed(self):
        output = io.StringIO()
        with (
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(output),
        ):
            self.reconciler.run_once()
        self.assertNotIn("synthetic", output.getvalue())
        self.assertEqual(
            sorted(path.name for path in self.directory.iterdir()),
            [
                "APPLICATION_API_KEY",
                "JELLYFIN_ADMIN_PASSWORD",
                "LDAP_BIND_PASSWORD",
                "configuration.json",
            ],
        )


if __name__ == "__main__":
    unittest.main()
