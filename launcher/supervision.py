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
* The tray-quit poll body that consumes ``.quit_requested`` markers and
  flips the process-wide quit event observed by every wizard-mode wait
  loop (issue #163).
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
from typing import Callable, Optional

logger = logging.getLogger(__name__)

IPC_POLL_INTERVAL_SECONDS = 0.25

# Module-level so the signal handler + test introspection can reach
# them without plumbing a context through every call site.
_broadcaster_supervisor = None  # type: ignore[var-annotated]
_caddy_supervisor = None  # type: ignore[var-annotated]
_shutdown_event = threading.Event()
# Process-wide tray-quit event (issue #163). Set by the IPC poll
# thread when a valid ``.quit_requested`` marker is observed; polled
# by every wizard-mode wait loop via :func:`wait_or_quit`.
_quit_requested = threading.Event()


def set_caddy_supervisor(supervisor) -> None:
    """Register the Caddy supervisor for shutdown + IPC-driven restart."""
    global _caddy_supervisor
    _caddy_supervisor = supervisor


def get_shutdown_event() -> threading.Event:
    """Return the shared shutdown event (signal handler + poll loop)."""
    return _shutdown_event


def get_quit_requested_event() -> threading.Event:
    """Return the process-wide tray-quit event (issue #163).

    The IPC poll thread sets this event the moment a valid tray quit
    marker is observed. Every wizard-mode wait loop polls it via
    :func:`wait_or_quit` so the user's "Quit Sethlans" click is honored
    even while the launcher is parked in the wizard wait phase.
    """
    return _quit_requested


def wait_or_quit(timeout: float) -> bool:
    """Sleep up to ``timeout`` seconds or return early on tray quit.

    Replaces ``time.sleep(poll_interval)`` inside wait loops so a tray
    quit marker fires within at most one ``poll_interval`` instead of
    burning the wait loop's wall-clock budget.

    Returns ``True`` if the quit event has been set (caller should
    abort with its quit-requested sentinel), ``False`` on plain
    timeout (caller should iterate normally).
    """
    return _quit_requested.wait(timeout=timeout)


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
    *,
    secret: Optional[str] = None,
    tray_pid_provider: Optional[Callable[[], int]] = None,
) -> threading.Thread:
    """Spawn the broadcaster/restart/tray-quit IPC poll thread.

    The thread polls every :data:`IPC_POLL_INTERVAL_SECONDS` for:

    1. ``broadcaster_params.json`` — published by the manager once
       ``runtime_init`` has populated ``runtime_state``. On first
       sighting the launcher starts the broadcaster.
    2. ``setup_restart_request.json`` — written by the Django setup
       wizard (under the setup mutation lock) when the wizard's
       network step changes ports. On sighting, the launcher
       re-templates the Caddyfile and restarts Caddy, then deletes
       the request file.
    3. ``.quit_requested`` (issue #163) — written by the tray helper.
       On a valid marker, sets :func:`get_quit_requested_event` so
       wizard-mode wait loops can abort cleanly. The data dir for the
       tray IPC marker is the launcher's per-user data dir, which is
       ``manager_data.parent``. ``secret`` and ``tray_pid_provider``
       are required for tray-quit consumption; if either is None the
       tray-quit branch is skipped (used by tests that only exercise
       the broadcaster/restart paths).

    Exits when :func:`get_shutdown_event` is set.
    """
    thread = threading.Thread(
        target=_ipc_poll_loop,
        args=(manager_data, secret, tray_pid_provider),
        name="launcher-ipc-poll",
        daemon=True,
    )
    thread.start()
    return thread


def _ipc_poll_loop(
    manager_data: Path,
    secret: Optional[str] = None,
    tray_pid_provider: Optional[Callable[[], int]] = None,
) -> None:
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
        _maybe_observe_tray_quit(manager_data, secret, tray_pid_provider)

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


def _maybe_observe_tray_quit(
    manager_data: Path,
    secret: Optional[str],
    tray_pid_provider: Optional[Callable[[], int]],
) -> None:
    """Consume any pending ``.quit_requested`` marker (issue #163).

    On a valid quit marker, sets the process-wide quit event so the
    wizard-mode wait loops can abort cleanly. Restart markers stay
    routed through the existing main-loop path; we only act on the
    quit target here.
    """
    if secret is None or tray_pid_provider is None:
        return
    if _quit_requested.is_set():
        return
    try:
        tray_pid = tray_pid_provider()
    except Exception:
        logger.exception("tray_pid_provider raised; skipping tray-quit poll")
        return
    # ``manager_data`` is ``<data_dir>/manager``; tray markers live in
    # the parent data dir alongside ``.setup_complete`` etc.
    data_dir = manager_data.parent
    try:
        from launcher import tray_ipc
        quit_target, _ = tray_ipc.consume_pending_ipc(
            data_dir, secret, tray_pid,
        )
    except Exception:
        logger.exception("Failed to poll tray IPC markers")
        return
    if quit_target is not None:
        logger.info(
            "Tray quit observed (target=%s); signalling shutdown",
            quit_target,
        )
        _quit_requested.set()


# ------------------------------------------------------------------
# Test-only hooks
# ------------------------------------------------------------------

def _reset_for_tests() -> None:
    """Reset module-level state between tests (private)."""
    global _broadcaster_supervisor, _caddy_supervisor
    _broadcaster_supervisor = None
    _caddy_supervisor = None
    _shutdown_event.clear()
    _quit_requested.clear()


def _reset_quit_requested_for_tests() -> None:
    """Clear the tray-quit event between tests (private).

    Exposed so a per-test autouse fixture can isolate test runs even
    when other module state should persist.
    """
    _quit_requested.clear()


def _get_broadcaster_supervisor_for_tests() -> Optional[object]:
    """Return the currently-held broadcaster supervisor (tests only)."""
    return _broadcaster_supervisor
