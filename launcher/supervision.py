# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Launcher-side supervisor coordination.

This module owns:

* The module-level :class:`BroadcasterSupervisor` and Caddy supervisor
  references, so the launcher's ``finally`` can stop both.
* The IPC poll thread: it starts the broadcaster whenever the manager
  has published ``broadcaster_params.json``, none runs and shutdown has
  not begun, and it reads the tray's ``.quit_requested`` marker into the
  process-wide quit request (``launcher.quit_state``, re-exported here;
  issue #163). It reads the quit marker only: restart and reset markers
  stay on disk for the loop (issue #345). An error in one pass is
  logged and the thread goes on.
* :func:`stop_broadcaster`: stops the broadcaster and deletes
  ``broadcaster_params.json`` under the lock the poll thread starts it
  under, so a stale file cannot start it again. The loop calls it
  wherever the manager stops or is about to start; the next manager
  writes the file again.
* SIGTERM/SIGINT: the handler only sets the quit flag
  (``quit_state.signal_quit``: no lock, no logging); the loop then stops
  the children the way a tray Quit does, and ``main()``'s ``finally``
  stops the supervisors.
"""

from __future__ import annotations

import logging
import signal
import threading
from pathlib import Path
from typing import Callable, Optional

from launcher.quit_state import (  # noqa: F401 - re-exported
    QUIT_ALL, QUIT_MANAGER, clear_manager_quit, quit_requested, quit_target, request_quit, wait_or_quit,
    was_signalled,
)
from launcher import quit_state

logger = logging.getLogger(__name__)

IPC_POLL_INTERVAL_SECONDS = 0.25

_broadcaster_supervisor = None  # type: ignore[var-annotated]
_caddy_supervisor = None  # type: ignore[var-annotated]
# Guards starting the broadcaster (poll thread) against stopping it
# (loop thread, shutdown). Never taken in a signal handler.
_broadcaster_lock = threading.Lock()
_shutdown_event = threading.Event()


def set_caddy_supervisor(supervisor) -> None:
    """Register the Caddy supervisor so shutdown stops it."""
    global _caddy_supervisor
    _caddy_supervisor = supervisor


def get_caddy_supervisor():
    """Return the registered Caddy supervisor, or ``None``."""
    return _caddy_supervisor


def take_caddy_supervisor():
    """Unregister and return the Caddy supervisor (``None`` when none)."""
    global _caddy_supervisor
    supervisor, _caddy_supervisor = _caddy_supervisor, None
    return supervisor


def get_shutdown_event() -> threading.Event:
    """Return the shared shutdown event (poll loop, ``main()``'s ``finally``)."""
    return _shutdown_event


def get_quit_requested_event() -> threading.Event:
    """The event a tray quit sets; a signal sets only the flag (``quit_requested``)."""
    return quit_state.get_event()


def _take_broadcaster(params_dir: Optional[Path]):
    """Under the lock: unregister the broadcaster and delete its params file."""
    global _broadcaster_supervisor
    from launcher.broadcaster_supervisor import BROADCASTER_PARAMS_FILENAME

    with _broadcaster_lock:
        supervisor, _broadcaster_supervisor = _broadcaster_supervisor, None
        if params_dir is not None:
            try:
                (Path(params_dir) / BROADCASTER_PARAMS_FILENAME).unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                logger.warning("Could not delete the broadcaster params in %s: %s", params_dir, exc)
    return supervisor


def _stop(supervisor, join_timeout: float) -> None:
    if supervisor is None:
        return
    try:
        supervisor.stop(join_timeout=join_timeout)
    except Exception:
        logger.exception("Error stopping BroadcasterSupervisor")


def stop_broadcaster(params_dir: Path, join_timeout: float = 5.0) -> None:
    """Stop the broadcaster and delete ``broadcaster_params.json`` (module doc)."""
    _stop(_take_broadcaster(params_dir), join_timeout)


def shutdown_supervisors() -> None:
    """Stop the broadcaster and Caddy supervisors; idempotent.

    The shutdown event is set first, so the poll thread starts no
    broadcaster afterwards.
    """
    global _caddy_supervisor
    _shutdown_event.set()
    _stop(_take_broadcaster(None), 5.0)
    caddy, _caddy_supervisor = _caddy_supervisor, None
    if caddy is not None:
        try:
            caddy.stop(timeout=5.0)
        except Exception:
            logger.exception("Error stopping Caddy supervisor")


def _graceful_shutdown(signum, frame):
    """SIGTERM/SIGINT: one flag, no lock, no logging (``quit_state.signal_quit``)."""
    del signum, frame
    quit_state.signal_quit()


def install_signal_handlers() -> None:
    """Wire SIGTERM/SIGINT to the graceful-shutdown path."""
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _graceful_shutdown)
        except (ValueError, OSError) as exc:
            logger.debug("Could not install handler for %s: %s", sig, exc)


def start_ipc_poll_thread(
    settings_dir: Path,
    *,
    params_dir: Optional[Path] = None,
    secret: Optional[str] = None,
    tray_pid_provider: Optional[Callable[[], int]] = None,
) -> threading.Thread:
    """Start the daemon poll thread (see the module doc); it ends on the shutdown event.

    ``settings_dir`` holds the tray markers. ``params_dir`` holds
    ``broadcaster_params.json`` (default
    ``launcher.broadcaster_supervisor.broadcaster_params_dir()``). With
    no ``secret`` or ``tray_pid_provider`` the quit marker is not read.
    """
    if params_dir is None:
        from launcher.broadcaster_supervisor import broadcaster_params_dir
        params_dir = broadcaster_params_dir()
    thread = threading.Thread(
        target=_ipc_poll_loop,
        args=(Path(settings_dir), Path(params_dir), secret, tray_pid_provider),
        name="launcher-ipc-poll",
        daemon=True,
    )
    thread.start()
    return thread


def _poll_once(settings_dir, params_dir, secret, tray_pid_provider) -> None:
    try:
        _maybe_start_broadcaster(params_dir)
        _maybe_observe_tray_quit(settings_dir, secret, tray_pid_provider)
    except Exception:
        logger.exception("Launcher IPC poll failed; polling goes on")


def _ipc_poll_loop(settings_dir, params_dir, secret=None, tray_pid_provider=None) -> None:
    """Poll body for :func:`start_ipc_poll_thread`."""
    while not _shutdown_event.is_set():
        _poll_once(settings_dir, params_dir, secret, tray_pid_provider)
        if _shutdown_event.wait(IPC_POLL_INTERVAL_SECONDS):
            return


def _maybe_start_broadcaster(params_dir: Path) -> None:
    """Start the broadcaster from ``broadcaster_params.json`` when none runs."""
    global _broadcaster_supervisor
    from launcher.broadcaster_supervisor import BroadcasterSupervisor, read_broadcaster_params

    with _broadcaster_lock:
        if _shutdown_event.is_set():
            return
        if _broadcaster_supervisor is not None and _broadcaster_supervisor.is_running():
            return
        params = read_broadcaster_params(params_dir)
        if params is None:
            return
        if _broadcaster_supervisor is None:
            _broadcaster_supervisor = BroadcasterSupervisor()
        try:
            _broadcaster_supervisor.start_from_params(params)
        except Exception:
            logger.exception("Failed to start BroadcasterSupervisor; will retry")


def _maybe_observe_tray_quit(settings_dir, secret, tray_pid_provider) -> None:
    """Read a pending ``.quit_requested`` marker and request the quit (issue #163).

    Skipped while a quit is already requested, so a second click's
    marker stays on disk for the cascade's second-quit check.
    """
    if secret is None or tray_pid_provider is None or quit_requested():
        return
    tray_pid = tray_pid_provider()
    from launcher import tray_ipc
    target = tray_ipc.consume_quit_marker(settings_dir, secret, tray_pid)
    if target is not None:
        logger.info("Tray quit observed (target=%s)", target)
        request_quit(target)


# ------------------------------------------------------------------
# Test-only hooks
# ------------------------------------------------------------------

def _reset_for_tests() -> None:
    """Reset module-level state between tests (private)."""
    global _broadcaster_supervisor, _caddy_supervisor
    _broadcaster_supervisor = None
    _caddy_supervisor = None
    _shutdown_event.clear()
    quit_state.reset_for_tests()


def _reset_quit_requested_for_tests() -> None:
    """Clear the quit request between tests (private)."""
    quit_state.reset_for_tests()


def _get_broadcaster_supervisor_for_tests() -> Optional[object]:
    """Return the currently-held broadcaster supervisor (tests only)."""
    return _broadcaster_supervisor
