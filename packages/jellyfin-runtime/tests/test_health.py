# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import health


class HealthTest(unittest.TestCase):
    def test_http_success_during_degraded_startup_is_not_ready(self):
        state = {"body": b"Degraded", "status": 200}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(state["status"])
                self.end_headers()
                self.wfile.write(state["body"])

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{server.server_port}/health"
        self.assertFalse(health.ready(url))
        state["body"] = b"Healthy"
        self.assertTrue(health.ready(url))
        state["status"] = 503
        self.assertFalse(health.ready(url))


if __name__ == "__main__":
    unittest.main()
