# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shutdown coordination for the Sethlans Reborn Worker Agent.

Owns the module-level shutdown event, the active-thread registry, the
signal handler, and the graceful-shutdown sequence. Split out of
``agent.py`` to keep that file under the 300-line cap.
"""

import logging
import signal
import sys
import threading
import time

from sethlans_worker_agent import capacity as capacity_module
from sethlans_worker_agent.web_ui import stop_server

logger = logging.getLogger(__name__)

# --- Shutdown Coordination ---
_shutdown_event = threading.Event()
_active_threads = []
_active_threads_lock = threading.Lock()
_caddy_supervisor = None  # set in agent._run_setup_phase (Phase 5b)

# Maximum time in seconds to wait for active job threads during shutdown.
SHUTDOWN_TIMEOUT_SECONDS = 30


def set_caddy_supervisor(supervisor):
    """Register the Caddy supervisor instance owned by the agent.

    Called from ``agent._run_setup_phase`` once the supervisor has been
    started; ``_graceful_shutdown`` then stops it before draining
    Waitress.
    """
    global _caddy_supervisor
    _caddy_supervisor = supervisor


def _prune_finished_threads():
    """Remove completed threads from the active threads list."""
    with _active_threads_lock:
        _active_threads[:] = [t for t in _active_threads if t.is_alive()]


def _shutdown_handler(signum, frame):
    """Signal handler for SIGINT/SIGTERM. Sets shutdown event."""
    sig_name = signal.Signals(signum).name
    logger.info(f"Received {sig_name}. Initiating graceful shutdown...")
    _shutdown_event.set()


def _wait_for_active_threads():
    """Wait for active job threads to complete, up to the timeout."""
    with _active_threads_lock:
        threads_to_join = list(_active_threads)

    if not threads_to_join:
        logger.info("No active job threads to wait for.")
        return

    logger.info(
        f"Waiting up to {SHUTDOWN_TIMEOUT_SECONDS}s for "
        f"{len(threads_to_join)} active job thread(s) to finish..."
    )

    deadline = time.monotonic() + SHUTDOWN_TIMEOUT_SECONDS
    for t in threads_to_join:
        remaining = max(0, deadline - time.monotonic())
        t.join(timeout=remaining)
        if t.is_alive():
            logger.warning(f"Thread '{t.name}' did not finish within the shutdown timeout.")
        else:
            logger.info(f"Thread '{t.name}' finished successfully.")


# --- Graceful Shutdown ---
def _graceful_shutdown():
    """Run the graceful shutdown sequence (server + threads + drift)."""
    logger.info("Shutdown event set. Stopping polling loop.")
    # Phase 5b: stop Caddy first so new requests stop flowing, then
    # drain Waitress.
    if _caddy_supervisor is not None:
        _caddy_supervisor.stop()
    stop_server()
    _wait_for_active_threads()
    logger.info("Sethlans Reborn Worker Agent shut down cleanly.")

    drift_code = capacity_module.get_drift_exit_code()
    if drift_code is not None:
        logger.critical(
            "Exiting with code %d due to GPU drift detection.", drift_code,
        )
        sys.exit(drift_code)
