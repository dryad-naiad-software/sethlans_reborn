# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Browser-opening and startup-banner helpers for the launcher.

Kept separate from ``run_launcher.py`` to stay under the 300-line
ceiling. Stdlib-only.

Clipboard: the launcher does NOT initialize a ``QGuiApplication``
(Qt lives in the tray helper, a separate subprocess), so the
Qt-based ``shared.tray.clipboard`` helper cannot be used here. The
native helper in ``launcher/clipboard.py`` shells out to
``pbcopy`` / ``clip`` / ``wl-copy`` / ``xclip`` / ``xsel``
instead. See GitHub issue #88.
"""

from __future__ import annotations

import hashlib
import os
import platform
import sys
import webbrowser
from pathlib import Path

# Import kept module-level so tests can monkey-patch a single
# attribute; never called with the token on argv.
from launcher.clipboard import copy_to_clipboard_native


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
    host: str = "localhost",
) -> bool:
    """Print the setup URL + token banner per setup-token-entry FR-14.

    Returns ``True`` iff the token was successfully copied to the
    clipboard (used by the caller to pick the last banner line).
    """
    url = f"https://{host}:{port}{wizard_path}"
    copy_ok = False
    if setup_token:
        try:
            copy_ok = copy_to_clipboard_native(setup_token)
        except Exception:  # defensive; helper never raises
            copy_ok = False

    bar = "=" * 63
    print(bar)
    print("  Sethlans Setup")
    print(f"  URL:    {url}")
    if setup_token:
        print(f"  Token:  {setup_token}")
        if copy_ok:
            print("  (Token copied to clipboard)")
        else:
            print("  (Copy the token above manually.)")
    print(bar)

    fp = compute_cert_fingerprint(data_dir)
    if fp:
        print(f"Cert fingerprint: sha256:{fp}", file=sys.stderr)
    else:
        print(
            "Cert fingerprint: (not yet generated; check after "
            "manager starts)",
            file=sys.stderr,
        )
    return copy_ok


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
    delivered via banner + clipboard instead.
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
