# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT
"""Check Jellyfin's complete startup state, rather than HTTP status alone."""

import sys
import urllib.error
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def ready(url="http://127.0.0.1:8096/health"):
    try:
        opener = urllib.request.build_opener(NoRedirect())
        with opener.open(url, timeout=2) as response:
            return response.status == 200 and response.read(64).strip() == b"Healthy"
    except urllib.error.HTTPError as error:
        error.close()
        return False
    except OSError:
        return False


if __name__ == "__main__":
    sys.exit(0 if ready() else 1)
