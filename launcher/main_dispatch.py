# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Main-orchestration dispatch helpers for the launcher (issue #203).

Extracted from :mod:`launcher.run_launcher` so that file stays under
the 300-line ceiling (BLOCKER-1, spec v2). Owns two routing helpers:

* :func:`_pre_orchestration_setup` — common pre-orchestration wiring
  (signal handlers, tray spawn, IPC poll thread).
* :func:`_run_orchestration` — wizard-mode vs normal-mode dispatch.
  After issue #203 it now FALLS THROUGH to :func:`run_normal_mode`
  when the wizard returns rc=0, so Caddy + manager keep running for
  the lifetime of the launcher process (was: returned immediately
  after the wizard, orphaning the manager and tearing down Caddy).
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Callable, Optional

from launcher import orchestration, supervision, tray_ipc, wizard_orchestration

logger = logging.getLogger(__name__)


def _is_setup_complete(data_dir: Path) -> bool:
    return (data_dir / ".setup_complete").exists()


def _run_orchestration(
    data_dir: Path,
    args,
    tray: Optional[subprocess.Popen],
    secret: str,
    *,
    bootstrap_first_run: Callable[[Path], Path],
    start_component: Callable[..., subprocess.Popen],
    on_cold_boot_ready: Optional[Callable[[], None]] = None,
    on_startup_failed: Optional[Callable[[str, str], None]] = None,
) -> int:
    """Route to wizard mode (first run) or normal mode (post-setup).

    Issue #203 fix: when the wizard returns rc=0, fall through to
    :func:`orchestration.run_normal_mode` rather than returning
    immediately. ``run_normal_mode`` owns the Caddy supervisor + the
    ``while True`` supervision loop; returning here would let
    ``main()``'s ``finally`` tear Caddy down, leaving the manager
    bound to loopback only.
    """
    if not _is_setup_complete(data_dir):
        rc = wizard_orchestration.run_wizard_mode(
            data_dir, args, bootstrap_first_run, start_component,
            on_cold_boot_ready=on_cold_boot_ready,
            on_startup_failed=on_startup_failed,
        )
        if rc != 0:
            return rc
        # FR-LOOP5: ensure manager.ini exists before run_normal_mode
        # reads it via _resolve_caddy_public_port. _bootstrap_first_run
        # is idempotent (no-op if the file already exists).
        bootstrap_first_run(data_dir)
        # FR-LOOP2 defensive guard: the wizard returning 0 should have
        # written ``.setup_complete`` via the apply pipeline; if not,
        # abort rather than spawn manager + Caddy on a half-set-up box.
        if not _is_setup_complete(data_dir):
            logger.error(
                "wizard returned rc=0 but .setup_complete is missing; "
                "aborting (issue #203 defensive guard)"
            )
            return 1
    return orchestration.run_normal_mode(
        data_dir, args, tray, secret, start_component,
        on_cold_boot_ready=on_cold_boot_ready,
        on_startup_failed=on_startup_failed,
    )


def _pre_orchestration_setup(
    data_dir: Path,
    spawn_tray: Callable[[Path, str], subprocess.Popen],
):
    """Common pre-orchestration wiring (signals, tray, IPC poll).

    ``spawn_tray`` is injected so :mod:`launcher.run_launcher` can
    pass its private ``_spawn_tray`` helper without dragging the tray
    spawn logic (and its diagnostics-aware ``sys.exit``) into this
    module.
    """
    supervision.install_signal_handlers()
    tray_ipc.sweep_stale_markers(data_dir)
    secret = tray_ipc.generate_secret()
    tray = spawn_tray(data_dir, secret)
    manager_data = data_dir / "manager"
    manager_data.mkdir(parents=True, exist_ok=True)
    # #163: poll thread consumes .quit_requested during wizard mode.
    supervision.start_ipc_poll_thread(
        manager_data, secret=secret,
        tray_pid_provider=lambda: tray.pid if tray is not None else -1,
    )
    return tray, secret


__all__ = ["_is_setup_complete", "_pre_orchestration_setup", "_run_orchestration"]
