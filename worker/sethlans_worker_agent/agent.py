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
import sys

from sethlans_worker_agent import config
from sethlans_worker_agent import runtime_state  # noqa: F401  # bind boot_id at agent-module import time (FR-RT-5)
from sethlans_worker_agent.agent_logging import configure_logging
from sethlans_worker_agent.web_ui import start_server

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
    from sethlans_worker_agent.agent_caddy import build_caddy_supervisor
    supervisor = build_caddy_supervisor(cert_path, key_path)
    supervisor.start()
    agent_shutdown.set_caddy_supervisor(supervisor)

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
            if agent_shutdown._caddy_supervisor is not None and (
                agent_shutdown._caddy_supervisor.error_event.is_set()
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


# --- Backward-compat re-exports ---
# Tests and external callers may still import these from this module.
from sethlans_worker_agent import agent_shutdown  # noqa: E402
from sethlans_worker_agent.agent_shutdown import (  # noqa: F401, E402
    _shutdown_event,
    _active_threads,
    _active_threads_lock,
    SHUTDOWN_TIMEOUT_SECONDS,
    _prune_finished_threads,
    _shutdown_handler,
    _wait_for_active_threads,
    _graceful_shutdown,
)
from sethlans_worker_agent.agent_loop import (  # noqa: F401, E402
    _should_skip_polling,
    _try_register_worker,
    _run_loop_iteration,
)


if __name__ == '__main__':
    main()
