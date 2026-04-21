# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Launcher-side supervisor coordination (manager spec Phase 3).

Extracted from :mod:`launcher.run_launcher` to respect the 300-line
ceiling. This module owns:

* The module-level :class:`BroadcasterSupervisor` + Caddy supervisor
  globals that back the signal-handler shutdown path.
* The IPC poll loop that waits for
  ``broadcaster_params.json`` and ``setup_restart_request.json``.
* Signal wiring for SIGTERM/SIGINT that unwinds both supervisors
  before the existing cascade tears down children.

The launcher holds only references into this module; tests and the
shutdown path never touch supervisor objects directly.
"""

from __future__ import annotations

import logging
import signal
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

IPC_POLL_INTERVAL_SECONDS = 0.25

# Module-level so the signal handler + test introspection can reach
# them without plumbing a context through every call site.
_broadcaster_supervisor = None  # type: ignore[var-annotated]
_caddy_supervisor = None  # type: ignore[var-annotated]
_shutdown_event = threading.Event()


def set_caddy_supervisor(supervisor) -> None:
    """Register the Caddy supervisor for shutdown + IPC-driven restart."""
    global _caddy_supervisor
    _caddy_supervisor = supervisor


def get_shutdown_event() -> threading.Event:
    """Return the shared shutdown event (signal handler + poll loop)."""
    return _shutdown_event


def shutdown_supervisors() -> None:
    """Stop the broadcaster + Caddy supervisors if running.

    Idempotent; safe to call from both the signal handler and the
    ``finally:`` block in :func:`launcher.run_launcher.main`.
    """
    global _broadcaster_supervisor, _caddy_supervisor
    if _broadcaster_supervisor is not None:
        try:
            _broadcaster_supervisor.stop(join_timeout=5.0)
        except Exception:
            logger.exception("Error stopping BroadcasterSupervisor")
        _broadcaster_supervisor = None
    if _caddy_supervisor is not None:
        try:
            _caddy_supervisor.stop(timeout=5.0)
        except Exception:
            logger.exception("Error stopping Caddy supervisor")
        _caddy_supervisor = None


def _graceful_shutdown(signum, frame):
    """POSIX/Windows signal handler for SIGTERM/SIGINT."""
    del frame
    logger.info(
        "Launcher received signal %s; initiating graceful shutdown",
        signum,
    )
    _shutdown_event.set()
    shutdown_supervisors()


def install_signal_handlers() -> None:
    """Wire SIGTERM/SIGINT to the graceful-shutdown path."""
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _graceful_shutdown)
        except (ValueError, OSError) as exc:
            logger.debug(
                "Could not install handler for %s: %s", sig, exc,
            )


def start_ipc_poll_thread(
    manager_data: Path,
) -> threading.Thread:
    """Spawn the broadcaster/restart IPC poll thread.

    The thread polls every :data:`IPC_POLL_INTERVAL_SECONDS` for:

    1. ``broadcaster_params.json`` — published by the manager once
       ``runtime_init`` has populated ``runtime_state``. On first
       sighting the launcher starts the broadcaster.
    2. ``setup_restart_request.json`` — written by the Django setup
       wizard (under the setup mutation lock) when the wizard's
       network step changes ports. On sighting, the launcher
       re-templates the Caddyfile and restarts Caddy, then deletes
       the request file.

    Exits when :func:`get_shutdown_event` is set.
    """
    thread = threading.Thread(
        target=_ipc_poll_loop,
        args=(manager_data,),
        name="launcher-ipc-poll",
        daemon=True,
    )
    thread.start()
    return thread


def _ipc_poll_loop(manager_data: Path) -> None:
    """Poll body for :func:`start_ipc_poll_thread`."""
    # Imported inside the loop so a frozen launcher missing manager
    # modules during early boot doesn't crash the main thread.
    from launcher.broadcaster_supervisor import (
        BroadcasterSupervisor,
        read_broadcaster_params,
    )
    global _broadcaster_supervisor

    broadcaster_started = False
    while not _shutdown_event.is_set():
        if not broadcaster_started and _broadcaster_supervisor is None:
            _broadcaster_supervisor = BroadcasterSupervisor()
        if not broadcaster_started:
            params = read_broadcaster_params(manager_data)
            if params is not None:
                try:
                    _broadcaster_supervisor.start_from_params(params)
                    broadcaster_started = True
                except Exception:
                    logger.exception(
                        "Failed to start BroadcasterSupervisor; "
                        "will retry"
                    )

        _maybe_apply_caddy_restart_request(manager_data)

        if _shutdown_event.wait(IPC_POLL_INTERVAL_SECONDS):
            return


def _maybe_apply_caddy_restart_request(manager_data: Path) -> None:
    """Check the IPC request file and, if present, restart Caddy."""
    from launcher.caddy_launcher import apply_restart_request
    from launcher.setup_helpers import (
        clear_setup_restart_request,
        read_setup_restart_request,
    )

    if _caddy_supervisor is None:
        return
    req = read_setup_restart_request(manager_data)
    if req is None:
        return
    try:
        apply_restart_request(_caddy_supervisor, req)
    except Exception:
        logger.exception("Failed to apply wizard Caddy restart request")
    finally:
        clear_setup_restart_request(manager_data)


# ------------------------------------------------------------------
# Test-only hooks
# ------------------------------------------------------------------

def _reset_for_tests() -> None:
    """Reset module-level state between tests (private)."""
    global _broadcaster_supervisor, _caddy_supervisor
    _broadcaster_supervisor = None
    _caddy_supervisor = None
    _shutdown_event.clear()


def _get_broadcaster_supervisor_for_tests() -> Optional[object]:
    """Return the currently-held broadcaster supervisor (tests only)."""
    return _broadcaster_supervisor
