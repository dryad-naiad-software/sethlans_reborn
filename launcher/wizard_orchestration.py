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
from pathlib import Path
from typing import Callable, Optional

from launcher import (
    supervision,
    wizard_caddy_lifecycle,
    wizard_caddy_wiring,
    wizard_dir,
    wizard_done_watch,
    wizard_runtime,
)
from launcher.browser_launch import open_browser, print_setup_banner
from launcher.cold_boot import safe_invoke
from launcher.health_probe import QuitRequested, wait_for_health

logger = logging.getLogger(__name__)

# FR-L7a polling cadence + default idle timeout — re-exported from
# :mod:`launcher.wizard_done_watch` so callers / tests still find them
# at the top of this module.
WIZARD_DONE_POLL_INTERVAL = wizard_done_watch.WIZARD_DONE_POLL_INTERVAL
DEFAULT_WIZARD_IDLE_TIMEOUT = wizard_done_watch.DEFAULT_WIZARD_IDLE_TIMEOUT

WIZARD_PATH = "/"
SETUP_TOKEN_FILENAME = ".setup_token"
IPC_SECRET_FILENAME = ".ipc_secret"
# Issue #172: persistent (non-dotted) copy of the setup token, read by
# the system tray's ``Copy Setup Token`` action. The wizard subprocess
# consumes-and-unlinks ``.setup_token`` at startup (FR-W6/SEC-MED-11),
# so the tray needs a separate file that survives wizard runtime.
# Removed at handoff by ``cleanup_wizard_dir`` (FR-L13).
TRAY_SETUP_TOKEN_FILENAME = "setup_token"


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
    """Write the FR-L3a / FR-L4a chmod-600 secret files.

    Three files are produced:

    * ``.setup_token`` — consumed and unlinked by the wizard subprocess
      at startup (FR-W6 / SEC-MED-11).
    * ``.ipc_secret`` — same lifecycle as ``.setup_token``.
    * ``setup_token`` (no dot) — persistent copy for the system tray's
      ``Copy Setup Token`` action (#172). Identical content; same
      chmod-600 / Windows ACL hardening. Removed at handoff by
      ``cleanup_wizard_dir`` (FR-L13).
    """
    target_dir = wizard_dir.ensure_wizard_dir(data_dir)
    token_bytes = setup_token.encode("utf-8")
    wizard_dir.write_secret_file(
        target_dir / SETUP_TOKEN_FILENAME, token_bytes,
    )
    wizard_dir.write_secret_file(
        target_dir / IPC_SECRET_FILENAME, ipc_secret,
    )
    wizard_dir.write_secret_file(
        target_dir / TRAY_SETUP_TOKEN_FILENAME, token_bytes,
    )


# ---- Wizard port file (diagnostics, FR-W3) --------------------------------
#
# Issue #170 split the port file into two concerns; helpers live in
# :mod:`launcher.wizard_caddy_lifecycle`. Backwards-compat aliases
# below keep older callers + tests on a stable surface.

_read_wizard_port = wizard_caddy_lifecycle.read_wizard_loopback_port
_wait_for_wizard_port = wizard_caddy_lifecycle.wait_for_wizard_loopback_port


# ---- .wizard_done watch (FR-L7a) ------------------------------------------
# Implementation lives in :mod:`launcher.wizard_done_watch`; re-exported
# here so the public API surface stays stable for callers/tests.
wait_for_wizard_done = wizard_done_watch.wait_for_wizard_done


# ---- Wizard URL surfacing (FR-L3 / FR-L11) --------------------------------

def surface_wizard_url(
    port: int, setup_token: str, data_dir: Path,
    no_browser: bool, print_url: bool,
) -> None:
    """Print banner / open browser per FR-L3 + FR-L11 semantics."""
    print_setup_banner(port, WIZARD_PATH, setup_token, data_dir)
    open_browser(port, no_browser, print_url, WIZARD_PATH, None)


# ---- Top-level orchestration entry ----------------------------------------

def _wizard_health_url(port: int) -> str:
    """FR-7: wizard's own /api/health/ URL for cold-boot health check.

    Post-#170: ``port`` is Caddy's public TLS port (default 8100).
    """
    return f"https://127.0.0.1:{port}/api/health/"


# Caddy supervisor teardown lives in :mod:`launcher.wizard_caddy_lifecycle`.
_stop_wizard_caddy = wizard_caddy_lifecycle.stop_wizard_caddy


def _quit_cleanup(
    wizard_proc: Optional[subprocess.Popen],
    on_cold_boot_ready: Optional[Callable[[], None]],
    caddy_supervisor=None,
) -> int:
    """Cascade teardown when a tray quit fires during wizard mode.

    Issue #163: tray quit is a *user-initiated* shutdown, so fire
    ``on_cold_boot_ready`` (NOT ``on_startup_failed``) — the splash
    error card MUST NOT appear. Issue #170 FR-8: wizard subprocess
    first, then Caddy supervisor (reverse of startup order).
    """
    safe_invoke(on_cold_boot_ready)
    wizard_runtime.terminate_wizard(wizard_proc)
    _stop_wizard_caddy(caddy_supervisor)
    return 0


def run_wizard_mode(
    data_dir: Path,
    args,
    bootstrap_first_run: Callable[[Path], Path],
    start_component: Callable[..., subprocess.Popen],
    *,
    on_cold_boot_ready: Optional[Callable[[], None]] = None,
    on_startup_failed: Optional[Callable[[str, str], None]] = None,
    idle_timeout: float = DEFAULT_WIZARD_IDLE_TIMEOUT,
) -> int:
    """First-run wizard hand-off (FR-L1 → FR-L13). Returns exit code.

    Issue #170 sequencing:
      1. Sweep + ensure wizard dir, write secrets.
      2. Generate the wizard TLS cert at ``<data_dir>/wizard/tls/``.
      3. Spawn the wizard subprocess (plain HTTP loopback).
      4. Wait for the wizard's loopback port file.
      5. Start the wizard Caddy supervisor (TLS termination).
      6. wait_for_health against the Caddy public port.
      7. Write Caddy's public port to ``<data_dir>/wizard/port``.
      8. surface_wizard_url + wait_for_wizard_done as before.
    """
    wizard_dir.sweep_stale_markers(data_dir)
    wizard_dir.ensure_wizard_dir(data_dir)

    setup_token = generate_setup_token()
    ipc_secret = generate_ipc_secret()
    write_secret_files(data_dir, setup_token, ipc_secret)
    logger.info("Wrote .setup_token and .ipc_secret (FR-L3a / FR-L4a)")

    # Issue #170 FR-3: generate the cert before Caddy starts so the
    # supervisor's cert file exists at start time.
    cert_failure = wizard_caddy_lifecycle.generate_wizard_cert_or_fail(
        data_dir, on_startup_failed, safe_invoke,
    )
    if cert_failure is not None:
        return cert_failure

    wizard_proc = start_component("wizard")
    logger.info("wizard spawned (pid=%s)", wizard_proc.pid)

    quit_event = supervision.get_quit_requested_event()
    caddy_supervisor = None

    # FR-13 — two independent budgets: 10 s port-file discovery, then a
    # separate 30 s health budget. No shared wall clock between them.
    loopback_port = wizard_caddy_lifecycle.wait_for_wizard_loopback_port(
        data_dir, wizard_proc, timeout=10.0,
    )
    # Issue #163: distinguish a tray-quit abort from a real port-file
    # timeout — both surface as ``None`` from the helper, so we check
    # the event explicitly here.
    if loopback_port is None and quit_event.is_set():
        return _quit_cleanup(wizard_proc, on_cold_boot_ready)
    if loopback_port is None:
        # MED-4: no silent fallback — bind may have landed on any
        # candidate or the wizard may have crashed. Treat as handshake
        # failure (the wizard's own log records the cause).
        logger.error(
            "wizard did not write loopback_port within 10s; aborting "
            "handoff",
        )
        safe_invoke(
            on_startup_failed,
            "wizard did not write loopback_port within 10 s", "",
        )
        wizard_runtime.terminate_wizard(wizard_proc)
        return wizard_runtime.wizard_failure_exit("wizard_no_port_file")

    # Issue #170 FR-4: start Caddy now that we know the wizard's
    # loopback port; the supervisor binds the public TLS port and
    # reverse-proxies to the loopback.
    caddy_supervisor, caddy_failure = (
        wizard_caddy_lifecycle.start_wizard_caddy_or_fail(
            data_dir, loopback_port, wizard_proc,
            on_startup_failed, safe_invoke,
        )
    )
    if caddy_failure is not None:
        return caddy_failure

    public_port = wizard_caddy_wiring.WIZARD_PUBLIC_TLS_PORT

    # FR-7: ``proc=wizard_proc`` lets the probe detect a wizard crash
    # within one poll interval (~250 ms) instead of the full 30 s.
    try:
        healthy = wait_for_health(
            _wizard_health_url(public_port), wizard_proc,
        )
    except QuitRequested:
        # AC-NoErrorCard: tray quit during cold-boot health probe
        # dismisses the splash via the success path.
        return _quit_cleanup(
            wizard_proc, on_cold_boot_ready, caddy_supervisor,
        )
    if not healthy:
        # FR-11(c): emit startup_failed BEFORE terminate so the
        # splash error card appears within ~250 ms of budget expiry.
        safe_invoke(
            on_startup_failed,
            "wizard did not become healthy within 30 s", "",
        )
        wizard_runtime.terminate_wizard(wizard_proc)
        _stop_wizard_caddy(caddy_supervisor)
        return wizard_runtime.wizard_failure_exit("wizard_health_timeout")

    # FR-7 (issue #170): publish Caddy's public port for tray + browser
    # consumers BEFORE we surface the URL.
    wizard_caddy_lifecycle.write_wizard_public_port_file(
        data_dir, public_port,
    )

    # FR-12: dismiss splash and open browser only after health success.
    safe_invoke(on_cold_boot_ready)
    surface_wizard_url(
        public_port, setup_token, data_dir,
        getattr(args, "no_browser", False),
        getattr(args, "print_url", False),
    )

    payload, reason = wait_for_wizard_done(
        wizard_proc, data_dir, ipc_secret, idle_timeout=idle_timeout,
    )
    return _finish_after_done(
        payload, reason, wizard_proc, caddy_supervisor,
        data_dir, ipc_secret,
        bootstrap_first_run, start_component,
    )


def _finish_after_done(
    payload, reason, wizard_proc, caddy_supervisor,
    data_dir, ipc_secret,
    bootstrap_first_run, start_component,
) -> int:
    """Handle the post-``wait_for_wizard_done`` branches.

    Extracted from :func:`run_wizard_mode` to keep that function below
    the cyclomatic-complexity ceiling (flake8 C901). The branches are:

    * ``"quit_requested"`` — tray quit during the URL banner phase;
      stop wizard then Caddy (FR-8) and return 0.
    * any other non-``"done"`` reason — failure exit; stop Caddy.
    * ``"done"`` — hand off to the runtime and tear down Caddy after.
    """
    if reason == "quit_requested":
        wizard_runtime.terminate_wizard(wizard_proc)
        _stop_wizard_caddy(caddy_supervisor)
        return 0
    if reason != "done":
        if reason == "idle_timeout":
            wizard_runtime.terminate_wizard(wizard_proc)
        _stop_wizard_caddy(caddy_supervisor)
        return wizard_runtime.wizard_failure_exit(reason)
    # FR-8 — splash dismissal is no longer driven from
    # ``hand_off_to_runtime``; the cold-boot trigger fired above. The
    # wizard subprocess is terminated inside ``hand_off_to_runtime``
    # before the wizard dir is rmtree'd; we tear down Caddy here so
    # the public TLS listener is released before runtime takes over.
    del bootstrap_first_run, start_component  # #203: spawn moved to run_normal_mode
    rc = wizard_runtime.hand_off_to_runtime(
        payload, data_dir, ipc_secret, wizard_proc,
    )
    _stop_wizard_caddy(caddy_supervisor)
    return rc
