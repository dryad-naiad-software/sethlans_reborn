# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Browser-opening and startup-banner helpers for the launcher.

Kept separate from ``run_launcher.py`` to stay under the 300-line
ceiling.  Stdlib only.
"""

from __future__ import annotations

import hashlib
import os
import platform
import sys
import webbrowser
from pathlib import Path


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


def compute_cert_fingerprint(data_dir: Path) -> str | None:
    """Colon-separated SHA-256 of manager's TLS cert, or None."""
    cert_path = data_dir / "manager" / "tls" / "cert.pem"
    if not cert_path.exists():
        return None
    try:
        raw = cert_path.read_bytes()
    except OSError:
        return None
    digest = hashlib.sha256(raw).hexdigest()
    return ":".join(
        digest[i:i + 2] for i in range(0, len(digest), 2)
    )


def print_setup_banner(
    port: int,
    wizard_path: str,
    setup_token: str | None,
    data_dir: Path,
) -> None:
    """Print Setup URL + cert fingerprint to stderr."""
    url = f"https://localhost:{port}{wizard_path}"
    if setup_token:
        url += f"?token={setup_token}"
    fp = compute_cert_fingerprint(data_dir)
    print(f"Setup URL: {url}", file=sys.stderr)
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
    setup_token: str | None = None,
) -> None:
    """Open browser to the wizard/dashboard URL (interactive only)."""
    url = f"https://localhost:{port}{path}"
    if setup_token:
        url += f"?token={setup_token}"
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
