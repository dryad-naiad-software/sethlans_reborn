# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Wizard Caddy lifecycle helpers (issue #170).

Extracted from :mod:`launcher.wizard_orchestration` to keep that module
under the 300-line ceiling. This module owns the wizard-channel state
that sits *between* the wizard subprocess and the wizard's Caddy
supervisor:

* ``loopback_port`` file polling — the wizard subprocess writes its
  plain-HTTP loopback port; the launcher reads it.
* ``port`` file write — Caddy's public TLS port is written by the
  launcher AFTER Caddy binds (so tray + browser open the right URL).
* TLS cert + key generation at ``<data_dir>/wizard/tls/`` via
  :func:`shared.cert_utils.generate_self_signed_cert`.
* ``stop_wizard_caddy`` — error-tolerant supervisor teardown.

Pure stdlib + project imports; no Django dependency.
"""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from typing import Optional

from launcher import supervision, wizard_dir
from shared.cert_utils import generate_self_signed_cert

logger = logging.getLogger(__name__)

# Issue #170 split the wizard port file into two concerns:
#   * ``<data_dir>/wizard/loopback_port`` — wizard subprocess writes;
#     reports its plain-HTTP loopback port.
#   * ``<data_dir>/wizard/port`` — launcher writes (post-Caddy-up);
#     reports Caddy's public TLS port (the URL the tray + browser
#     use, the URL the cold-boot probe polls).
WIZARD_LOOPBACK_PORT_FILENAME = "loopback_port"
WIZARD_PUBLIC_PORT_FILENAME = "port"


def read_wizard_loopback_port(data_dir: Path) -> Optional[int]:
    """Return the wizard's loopback port from its port file, or ``None``."""
    port_file = (
        wizard_dir.wizard_dir(data_dir) / WIZARD_LOOPBACK_PORT_FILENAME
    )
    try:
        raw = port_file.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def wait_for_wizard_loopback_port(
    data_dir: Path,
    wizard_proc: subprocess.Popen,
    timeout: float = 10.0,
    poll_interval: float = 0.25,
) -> Optional[int]:
    """Poll for the wizard's loopback port file until written or timeout.

    Returns the port int, or ``None`` if the wizard exits, the timeout
    elapses, or a tray-quit event fires (caller checks the quit event
    to distinguish from a real timeout).
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        port = read_wizard_loopback_port(data_dir)
        if port is not None:
            return port
        if wizard_proc.poll() is not None:
            return None
        # Issue #163: a tray quit observed mid-wait collapses to the
        # same ``None`` sentinel used for a port-file timeout — the
        # caller already routes that into the quit-cleanup path.
        if supervision.wait_or_quit(poll_interval):
            return None
    return None


def write_wizard_public_port_file(data_dir: Path, port: int) -> None:
    """Write Caddy's public TLS port to ``<data_dir>/wizard/port``.

    Best-effort: failures are logged but do not abort startup. The
    cold-boot probe already knows the port (the launcher passed it
    in) — this file exists for the tray + diagnostics surface.
    """
    target = (
        wizard_dir.wizard_dir(data_dir) / WIZARD_PUBLIC_PORT_FILENAME
    )
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(f"{port}\n", encoding="utf-8")
        tmp.replace(target)
    except OSError as exc:
        logger.warning(
            "Could not write wizard public-port file %s: %s", target, exc,
        )


def generate_wizard_cert(data_dir: Path) -> tuple[Path, Path]:
    """Issue #170 FR-3: generate the wizard's TLS cert + key for Caddy.

    Lives at ``<data_dir>/wizard/tls/{cert.pem,key.pem}`` (mirrors the
    manager layout). Idempotent in spirit — the helper overwrites the
    file unconditionally, but each first-run starts with a fresh
    wizard subdir anyway. Raises on cert-generation failure; the
    caller should treat that as a startup failure.
    """
    wizard_subdir = wizard_dir.wizard_dir(data_dir)
    cert_path = wizard_subdir / "tls" / "cert.pem"
    key_path = wizard_subdir / "tls" / "key.pem"
    generate_self_signed_cert(cert_path, key_path)
    return cert_path, key_path


def stop_wizard_caddy(supervisor) -> None:
    """Stop the wizard Caddy supervisor; tolerate teardown errors."""
    if supervisor is None:
        return
    try:
        supervisor.stop(timeout=5.0)
    except Exception:
        logger.exception("Error stopping wizard Caddy supervisor")


def start_wizard_caddy_or_fail(
    data_dir: Path,
    loopback_port: int,
    wizard_proc: subprocess.Popen,
    on_startup_failed,
    safe_invoke,
):
    """Start wizard Caddy, returning ``(supervisor, exit_code_on_fail)``.

    On success returns ``(supervisor, None)``; on failure returns
    ``(None, exit_code)`` after terminating the wizard subprocess and
    surfacing the splash error card. Keeps the caller's branching
    shallow.
    """
    from launcher import wizard_caddy_wiring, wizard_runtime
    try:
        supervisor = wizard_caddy_wiring.start_wizard_caddy_supervisor(
            data_dir, loopback_port,
        )
    except Exception as exc:
        logger.exception("Wizard Caddy supervisor failed to start")
        safe_invoke(
            on_startup_failed,
            f"wizard Caddy supervisor failed to start: {exc}", "",
        )
        wizard_runtime.terminate_wizard(wizard_proc)
        return None, wizard_runtime.wizard_failure_exit(
            "wizard_caddy_failed",
        )
    return supervisor, None


def generate_wizard_cert_or_fail(
    data_dir: Path,
    on_startup_failed,
    safe_invoke,
):
    """Generate the wizard cert, returning ``exit_code`` on failure (else None).

    Mirrors :func:`start_wizard_caddy_or_fail` for the cert-generation
    step. On success returns ``None``; on failure returns the launcher
    exit code after surfacing the splash error card.
    """
    from launcher import wizard_runtime
    try:
        generate_wizard_cert(data_dir)
    except Exception as exc:
        logger.exception("Wizard TLS cert generation failed")
        safe_invoke(
            on_startup_failed,
            f"wizard TLS cert generation failed: {exc}", "",
        )
        return wizard_runtime.wizard_failure_exit("wizard_cert_failed")
    return None
