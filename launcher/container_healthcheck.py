# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Docker ``HEALTHCHECK`` probe for the manager image (issue #298).

Probes ``https://127.0.0.1:<port>/api/health/`` where ``<port>`` is the
port Caddy actually listens on, resolved by the same function the
container entrypoint uses (``SETHLANS_MANAGER_PORT`` > settings file
``[server] port`` > 8080). A fixed 8080 reported a healthy container as
unhealthy whenever the port was changed.

Exit 0 when the endpoint answers 200, else 1 (Docker's contract).
"""

from __future__ import annotations

import http.client
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
for _path in (str(PROJECT_ROOT / "manager"), str(PROJECT_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from launcher.container_entrypoint import resolve_public_port  # noqa: E402
from sethlans_manager.tls_setup import get_config_file_path  # noqa: E402

TIMEOUT_SECONDS = 4.0


def health_url() -> str:
    """Return the health URL on the port Caddy serves."""
    port = resolve_public_port(get_config_file_path())
    return f"https://127.0.0.1:{port}/api/health/"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects: the health endpoint answers 200 directly."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # urllib then raises HTTPError for the 3xx


def build_opener(context: ssl.SSLContext) -> urllib.request.OpenerDirector:
    """Opener that ignores proxy env vars and does not follow redirects."""
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirect(),
        urllib.request.HTTPSHandler(context=context),
    )


def probe(url: str, timeout: float = TIMEOUT_SECONDS) -> bool:
    """Return True when ``url`` answers HTTP 200 itself.

    Loopback only, against the manager's own (often self-signed) cert,
    so the cert is not verified -- the same as ``curl -k``. Proxy
    settings are ignored and a redirect counts as unhealthy.
    """
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with build_opener(context).open(url, timeout=timeout) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as exc:  # any 3xx/4xx/5xx
        exc.close()
        return False
    except (OSError, ValueError, http.client.HTTPException):
        return False


def main() -> int:
    """Probe once; return Docker's health exit code."""
    try:
        url = health_url()
    except ValueError as exc:
        print(f"unhealthy: {exc}", file=sys.stderr)
        return 1
    if probe(url):
        return 0
    print(f"unhealthy: no 200 from {url}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
