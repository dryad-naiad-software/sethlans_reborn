# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Main entry point for the Sethlans Reborn Worker Agent.

Registers with the manager, sends heartbeats, polls for jobs, and
dispatches render threads. Supports graceful shutdown via signals.
"""

import argparse
import logging
import signal
import threading
import time
import sys
from sethlans_worker_agent import (
    job_processor, system_monitor, config, api_handler,
)
from sethlans_worker_agent import version_sync
from sethlans_worker_agent import capacity as capacity_module
from sethlans_worker_agent.agent_logging import configure_logging
from sethlans_worker_agent.web_ui import start_server, stop_server

# Logger creation is import-safe; configure_logging runs inside main().
logger = logging.getLogger(__name__)


def _parse_args(argv=None):
    """Parse CLI args. Must be called from main(), never at import time (issue #119)."""
    parser = argparse.ArgumentParser(description="Sethlans Reborn Worker Agent")
    parser.add_argument(
        '--loglevel', dest='loglevel',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
        default='INFO',
        help='Set the logging level for console and file output.'
    )
    return parser.parse_args(argv)


# --- Shutdown Coordination ---
_shutdown_event = threading.Event()
_active_threads = []
_active_threads_lock = threading.Lock()
_caddy_supervisor = None  # set in _run_setup_phase (Phase 5b)

# Maximum time in seconds to wait for active job threads during shutdown.
SHUTDOWN_TIMEOUT_SECONDS = 30


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


def _should_skip_polling():
    """Check if job polling should be skipped this cycle. Returns wait time or None."""
    if job_processor.is_paused():
        logger.debug("Worker is paused. Skipping job poll.")
        return config.JOB_POLLING_INTERVAL_SECONDS
    if api_handler.is_auth_failed():
        logger.warning("Authentication failed. Skipping job poll. Heartbeats will continue.")
        return config.HEARTBEAT_INTERVAL_SECONDS
    if not system_monitor.are_versions_ready():
        logger.warning("No required Blender versions installed yet. Skipping job poll.")
        return config.HEARTBEAT_INTERVAL_SECONDS

    # Schedule gate (FR-8): check if we are inside the claim window.
    from sethlans_worker_agent.idle_detection import schedule
    window_cfg = config.get_schedule_config()
    if not schedule.is_inside_claim_window(window_cfg):
        logger.debug("Outside claim window. Skipping job poll.")
        return config.JOB_POLLING_INTERVAL_SECONDS

    # Idle gate (FR-1, FR-2): check if the machine is idle.
    from sethlans_worker_agent.idle_detection import is_idle
    overrides = window_cfg.get('overrides_idle_detection', False)
    in_window = schedule.is_inside_claim_window(window_cfg)
    skip_idle = overrides and in_window
    if not skip_idle and not is_idle():
        logger.debug("Machine is not idle. Skipping job poll.")
        return config.JOB_POLLING_INTERVAL_SECONDS

    return None


def _try_register_worker():
    """Attempt registration. Returns the worker ID on success, else None."""
    logger.warning("Worker not registered with Manager. Attempting registration...")
    new_id = system_monitor.register_with_manager()
    if not new_id:
        logger.error("Failed to register with manager. Retrying in 30 seconds...")
        return None
    job_processor.init_capacity()
    return new_id


def _run_loop_iteration(worker_id):
    """One iteration of the main loop after the worker is registered.

    Sends the heartbeat, runs the drift check, processes pending
    downloads, then either polls for and dispatches a job or skips
    polling (paused / auth fail / versions not ready / capacity full).
    """
    active_jobs = job_processor.get_active_jobs_snapshot()
    is_busy = len(active_jobs) > 0

    # Heartbeats run regardless of pause state.
    system_monitor.send_heartbeat(is_busy=is_busy, active_jobs=active_jobs)

    # GPU drift check (FR-22, FR-23). Fires at most once per heartbeat
    # interval, not once per poll interval.
    job_processor.maybe_assert_gpu_count_unchanged()

    if not is_busy:
        version_sync.process_pending_downloads()
        version_sync.process_pending_removals(active_jobs)

    skip_wait = _should_skip_polling()
    if skip_wait is not None:
        _shutdown_event.wait(skip_wait)
        return

    # Capacity gate (FR-6). Source of truth is WorkerCapacity.is_full(),
    # NOT _active_jobs.
    if job_processor.capacity_is_full():
        logger.debug("Worker at capacity. Skipping poll.")
        _shutdown_event.wait(config.JOB_POLLING_INTERVAL_SECONDS)
        return

    thread = job_processor.get_and_claim_job(worker_id)
    if thread is not None:
        with _active_threads_lock:
            _active_threads.append(thread)

    logger.debug(
        f"Loop finished. Sleeping for {config.JOB_POLLING_INTERVAL_SECONDS} seconds."
    )
    _shutdown_event.wait(config.JOB_POLLING_INTERVAL_SECONDS)


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


def _run_setup_phase():
    """Handle TLS, web server start, and setup gate / wizard.

    Returns ``True`` if setup completed and the main loop should
    proceed, or ``False`` if shutdown was requested during setup.
    """
    # TLS setup — once, before the web server.
    from sethlans_worker_agent import tls_setup
    from shared.cert_utils import CertificateError
    try:
        cert_path, key_path, _fingerprint = tls_setup.setup_certificates()
    except CertificateError as e:
        logger.critical("TLS certificate error: %s", e)
        sys.exit(1)

    # Start Waitress first (plaintext loopback upstream), then Caddy
    # in front. Caddy crash-loops harmlessly if Waitress is not yet
    # accepting, but starting Waitress first removes the race.
    start_server(cert_path, key_path)
    global _caddy_supervisor
    from sethlans_worker_agent.agent_caddy import build_caddy_supervisor
    _caddy_supervisor = build_caddy_supervisor(cert_path, key_path)
    _caddy_supervisor.start()

    # Initialize setup gate (reads sentinel to decide mode).
    from sethlans_worker_agent.agent_setup import (
        initialize_setup_gate,
        run_first_run_wizard_if_needed,
        wait_for_browser_setup,
    )
    setup_done = initialize_setup_gate()

    if not setup_done:
        if sys.stdin.isatty():
            wait_for_browser_setup(
                _shutdown_event, config.UI_BIND_ADDRESS, config.UI_PORT,
            )
            if _shutdown_event.is_set():
                return False
        else:
            wizard_code = run_first_run_wizard_if_needed()
            if wizard_code != 0:
                sys.exit(wizard_code)
    else:
        wizard_code = run_first_run_wizard_if_needed()
        if wizard_code != 0:
            sys.exit(wizard_code)

    return True


# --- Main Application Logic ---
def main(argv=None):
    """Main loop: parse args, configure logging, register, heartbeat, poll, dispatch, shutdown."""
    args = _parse_args(argv)
    configure_logging(args.loglevel)

    signal.signal(signal.SIGINT, _shutdown_handler)
    signal.signal(signal.SIGTERM, _shutdown_handler)

    logger.info("Sethlans Reborn Worker Agent Starting...")

    # Start Windows session unlock monitor (FR-4c).
    from sethlans_worker_agent.idle_detection.session_win32 import (
        start_session_monitor,
    )
    start_session_monitor()

    if not _run_setup_phase():
        _graceful_shutdown()
        return

    worker_id = None

    while not _shutdown_event.is_set():
        try:
            if _caddy_supervisor is not None and (
                _caddy_supervisor.error_event.is_set()
            ):
                logger.critical(
                    "Caddy supervision failed — worker exiting"
                )
                _graceful_shutdown()
                sys.exit(1)
            _prune_finished_threads()
            if not worker_id:
                worker_id = _try_register_worker()
                if not worker_id:
                    _shutdown_event.wait(30)
                    continue
            _run_loop_iteration(worker_id)
        except Exception as e:
            logger.critical(
                f"An unhandled exception occurred in the main loop: {e}",
                exc_info=True
            )
            logger.info("Restarting main loop in 60 seconds...")
            _shutdown_event.wait(60)

    _graceful_shutdown()


if __name__ == '__main__':
    main()
