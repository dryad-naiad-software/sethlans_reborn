# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Browser-opening and startup-banner helpers for the launcher.

Kept separate from ``run_launcher.py`` to stay under the 300-line
ceiling.

The setup token is printed in the banner and never copied to the
clipboard automatically: the tray's Copy Setup Token does that when the
person clicks it (FR-SEC-1, SR nit 10). The certificate fingerprint is
the manager's own certificate, the one Caddy serves
(:func:`launcher.caddy_wiring.manager_tls_paths`).
"""

from __future__ import annotations

import hashlib
import os
import platform
import ssl
import sys
import webbrowser
from pathlib import Path
from typing import Optional


def is_headless() -> bool:
    """Detect headless / non-interactive session.

    * Linux / BSD: no ``DISPLAY`` and no ``WAYLAND_DISPLAY``.
    * Windows: conservative — treat missing ``SESSIONNAME`` as headless
      (service/daemon context).  Interactive console / RDP always
      sets ``SESSIONNAME``.
    * macOS: always interactive.
    """
    system = platform.system()
    if system == "Linux":
        return not (
            os.environ.get("DISPLAY")
            or os.environ.get("WAYLAND_DISPLAY")
        )
    if system == "Windows":
        return os.environ.get("SESSIONNAME", "") == ""
    return False


def compute_cert_fingerprint(cert_path: Optional[Path] = None) -> str | None:
    """Colon-separated SHA-256 of the manager's TLS cert (DER), or None.

    The hash is over the DER certificate, as browsers and the workers'
    pin (``shared.cert_utils.get_cert_fingerprint``) compute it, not over
    the PEM file (issue #349). ``cert_path`` defaults to the manager's
    own certificate. A missing or unparsable file gives None.
    """
    if cert_path is None:
        from launcher.caddy_wiring import manager_tls_paths
        cert_path, _key = manager_tls_paths()
    try:
        der = ssl.PEM_cert_to_DER_cert(cert_path.read_text(encoding="ascii"))
    except (OSError, ValueError):
        return None
    digest = hashlib.sha256(der).hexdigest()
    return ":".join(
        digest[i:i + 2] for i in range(0, len(digest), 2)
    )


def print_setup_banner(
    port: int,
    wizard_path: str,
    setup_token: str | None,
    cert_path: Optional[Path] = None,
    host: str = "localhost",
) -> None:
    """Print the setup URL, the token and the certificate's SHA-256 (SR nit 4)."""
    url = f"https://{host}:{port}{wizard_path}"
    bar = "=" * 63
    print(bar)
    print("  Sethlans Setup")
    print(f"  URL:    {url}")
    if setup_token:
        print(f"  Token:  {setup_token}")
        print("  (Copy it from here, or use Copy Setup Token in the tray.)")
    print(bar)

    fp = compute_cert_fingerprint(cert_path)
    if fp:
        print(f"Cert fingerprint: sha256:{fp}", file=sys.stderr)
    else:
        print(
            "Cert fingerprint: (not yet generated; check after "
            "manager starts)",
            file=sys.stderr,
        )


def open_browser(
    port: int,
    no_browser: bool,
    print_url: bool,
    path: str,
    setup_token: str | None = None,  # retained for API back-compat
    host: str = "localhost",
) -> None:
    """Open browser to the wizard/dashboard URL (interactive only).

    ``setup_token`` is ignored in v2 (setup-token-entry FR-13): the
    URL never contains ``?token=`` because Chrome strips the query
    string behind the self-signed-cert interstitial.  The token is
    delivered via the banner and the tray instead.
    """
    del setup_token  # intentionally unused; see docstring.
    url = f"https://{host}:{port}{path}"
    headless = is_headless()

    if print_url or headless:
        print(f"Sethlans is running at: {url}")
    if no_browser or print_url or headless:
        return
    try:
        webbrowser.open(url)
    except Exception as exc:
        print(f"Could not open browser: {exc}", file=sys.stderr)
        print(f"Navigate manually to: {url}", file=sys.stderr)
