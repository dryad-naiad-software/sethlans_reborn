# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Wizard-mode orchestration entry point (FR-L1 → FR-L13).

Implements the launcher's first-run flow: spawn wizard, watch for
``.wizard_done`` marker, validate, hand off to the runtime per
``topology.json``. Helpers live in ``launcher/wizard_runtime.py`` so
this file stays under the 300-line limit.

threading-only (no asyncio in launcher).
"""

from __future__ import annotations

import logging
import secrets
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

from launcher import wizard_dir, wizard_ipc, wizard_runtime
from launcher.browser_launch import open_browser, print_setup_banner

logger = logging.getLogger(__name__)

# FR-L7a polling cadence and default idle timeout.
WIZARD_DONE_POLL_INTERVAL = 0.5
DEFAULT_WIZARD_IDLE_TIMEOUT = 30 * 60  # 30 minutes

WIZARD_PATH = "/"
SETUP_TOKEN_FILENAME = ".setup_token"
IPC_SECRET_FILENAME = ".ipc_secret"


# ---- Secrets generation + chmod-600 file writes ---------------------------

def generate_setup_token() -> str:
    """Generate the FR-L3 setup token (URL-safe, 32 bytes of entropy)."""
    return secrets.token_urlsafe(32)


def generate_ipc_secret() -> bytes:
    """Generate the FR-L4 IPC HMAC secret (32 bytes of entropy)."""
    return secrets.token_urlsafe(32).encode("ascii")


def write_secret_files(
    data_dir: Path, setup_token: str, ipc_secret: bytes,
) -> None:
    """Write the FR-L3a / FR-L4a chmod-600 secret files."""
    target_dir = wizard_dir.ensure_wizard_dir(data_dir)
    wizard_dir.write_secret_file(
        target_dir / SETUP_TOKEN_FILENAME, setup_token.encode("utf-8"),
    )
    wizard_dir.write_secret_file(
        target_dir / IPC_SECRET_FILENAME, ipc_secret,
    )


# ---- Wizard port file (diagnostics, FR-W3) --------------------------------

def _read_wizard_port(data_dir: Path) -> Optional[int]:
    port_file = wizard_dir.wizard_dir(data_dir) / "port"
    try:
        raw = port_file.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _wait_for_wizard_port(
    data_dir: Path,
    wizard_proc: subprocess.Popen,
    timeout: float = 10.0,
    poll_interval: float = 0.25,
) -> Optional[int]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        port = _read_wizard_port(data_dir)
        if port is not None:
            return port
        if wizard_proc.poll() is not None:
            return None
        time.sleep(poll_interval)
    return None


# ---- .wizard_done watch (FR-L7a) ------------------------------------------

def wait_for_wizard_done(
    wizard_proc: subprocess.Popen,
    data_dir: Path,
    ipc_secret: bytes,
    idle_timeout: float = DEFAULT_WIZARD_IDLE_TIMEOUT,
    poll_interval: float = WIZARD_DONE_POLL_INTERVAL,
) -> tuple[Optional[dict], str]:
    """Poll for ``.wizard_done`` until one of FR-L7a's exits fires.

    Returns ``(payload_or_None, reason)``. ``reason`` is one of
    ``"done"``, ``"wizard_failed"``, ``"wizard_no_handshake"``,
    ``"idle_timeout"``.
    """
    marker_path = (
        wizard_dir.wizard_dir(data_dir) / wizard_ipc.MARKER_WIZARD_DONE
    )
    deadline = time.monotonic() + idle_timeout
    while True:
        # Marker observation has highest precedence: a valid marker even
        # after wizard exit means the handshake completed.
        payload = wizard_ipc.read_marker(
            marker_path, ipc_secret, "wizard_done", data_dir,
        )
        if payload is not None:
            wizard_ipc.delete_marker(marker_path)
            logger.info("wizard_done observed (FR-L7)")
            return payload, "done"

        rc = wizard_proc.poll()
        if rc is not None:
            if rc != 0:
                logger.error(
                    "wizard exited non-zero (code %d) before .wizard_done",
                    rc,
                )
                return None, "wizard_failed"
            logger.error(
                "wizard exited 0 without writing .wizard_done; "
                "treating as handshake failure",
            )
            return None, "wizard_no_handshake"

        if time.monotonic() >= deadline:
            logger.warning(
                "Wizard idle-timeout (%.0fs) elapsed without .wizard_done",
                idle_timeout,
            )
            return None, "idle_timeout"

        time.sleep(poll_interval)


# ---- Wizard URL surfacing (FR-L3 / FR-L11) --------------------------------

def surface_wizard_url(
    port: int, setup_token: str, data_dir: Path,
    no_browser: bool, print_url: bool,
) -> None:
    """Print banner / open browser per FR-L3 + FR-L11 semantics."""
    print_setup_banner(port, WIZARD_PATH, setup_token, data_dir)
    open_browser(port, no_browser, print_url, WIZARD_PATH, None)


# ---- Top-level orchestration entry ----------------------------------------

def run_wizard_mode(
    data_dir: Path,
    args,
    bootstrap_first_run: Callable[[Path], Path],
    start_component: Callable[..., subprocess.Popen],
    *,
    on_manager_ready: Optional[Callable[[], None]] = None,
    idle_timeout: float = DEFAULT_WIZARD_IDLE_TIMEOUT,
) -> int:
    """First-run wizard hand-off (FR-L1 → FR-L13). Returns exit code."""
    wizard_dir.sweep_stale_markers(data_dir)
    wizard_dir.ensure_wizard_dir(data_dir)

    setup_token = generate_setup_token()
    ipc_secret = generate_ipc_secret()
    write_secret_files(data_dir, setup_token, ipc_secret)
    logger.info("Wrote .setup_token and .ipc_secret (FR-L3a / FR-L4a)")

    wizard_proc = start_component("wizard")
    logger.info("wizard spawned (pid=%s)", wizard_proc.pid)

    chosen_port = _wait_for_wizard_port(data_dir, wizard_proc, timeout=10.0)
    if chosen_port is None:
        # MED-4 (Phase F1): no silent fallback to 8100 — the wizard's
        # bind may have landed on any of 8100..8104 (FR-W3 port scan)
        # or it may have crashed. Either way, surfacing a wrong banner
        # URL would send the operator to the wrong page. Treat as
        # handshake failure; the wizard's own log records the cause.
        logger.error(
            "wizard did not write port file within 10s; aborting handoff",
        )
        wizard_runtime.terminate_wizard(wizard_proc)
        return wizard_runtime.wizard_failure_exit("wizard_no_port_file")
    surface_wizard_url(
        chosen_port, setup_token, data_dir,
        getattr(args, "no_browser", False),
        getattr(args, "print_url", False),
    )

    payload, reason = wait_for_wizard_done(
        wizard_proc, data_dir, ipc_secret, idle_timeout=idle_timeout,
    )

    if reason != "done":
        if reason == "idle_timeout":
            wizard_runtime.terminate_wizard(wizard_proc)
        return wizard_runtime.wizard_failure_exit(reason)

    return wizard_runtime.hand_off_to_runtime(
        payload, data_dir, ipc_secret, wizard_proc,
        bootstrap_first_run, start_component, on_manager_ready,
    )
