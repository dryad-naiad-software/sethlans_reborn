# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The main entry point for the Sethlans Reborn Worker Agent.

This script initializes the worker, handles command-line arguments, and
enters a loop to perform its core duties:
1. Registering with the central manager.
2. Sending periodic heartbeats to maintain a live connection.
3. Polling the manager for new render jobs.
4. Claiming and executing available jobs.

The agent supports graceful shutdown via SIGINT (Ctrl+C) or SIGTERM,
waiting for active job threads to finish before exiting.
"""

import argparse
import logging
from logging.handlers import RotatingFileHandler
import signal
import threading
import time
import sys
from sethlans_worker_agent import job_processor, system_monitor, config
from sethlans_worker_agent.web_ui import start_server, stop_server

# --- Argument Parsing ---
parser = argparse.ArgumentParser(description="Sethlans Reborn Worker Agent")
parser.add_argument(
    '--loglevel',
    dest='loglevel',
    choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
    default='INFO',
    help='Set the logging level for console and file output.'
)
args = parser.parse_args()

# --- Logging Setup ---
# Ensure the log directory exists
config.WORKER_LOG_DIR.mkdir(exist_ok=True)
log_file_path = config.WORKER_LOG_DIR / 'worker.log'

# Get the root logger
root_logger = logging.getLogger()
root_logger.setLevel(getattr(logging, args.loglevel))

# Create a standard formatter
formatter = logging.Formatter(
    '[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Create and add the console handler
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(formatter)
root_logger.addHandler(console_handler)

# Create and add the rotating file handler
file_handler = RotatingFileHandler(
    log_file_path,
    maxBytes=5*1024*1024,  # 5 MB
    backupCount=3
)
file_handler.setFormatter(formatter)
root_logger.addHandler(file_handler)

# Get the logger for this module specifically
logger = logging.getLogger(__name__)

# --- Shutdown Coordination ---
_shutdown_event = threading.Event()
_active_threads = []
_active_threads_lock = threading.Lock()

# Maximum time in seconds to wait for active job threads during shutdown.
SHUTDOWN_TIMEOUT_SECONDS = 30


def _prune_finished_threads():
    """Remove completed threads from the active threads list."""
    with _active_threads_lock:
        _active_threads[:] = [t for t in _active_threads if t.is_alive()]


def _shutdown_handler(signum, frame):
    """
    Signal handler for SIGINT and SIGTERM.

    Sets the shutdown event to stop the main loop from polling for new
    jobs. The main loop is responsible for joining active threads.
    """
    sig_name = signal.Signals(signum).name
    logger.info(f"Received {sig_name}. Initiating graceful shutdown...")
    _shutdown_event.set()


def _wait_for_active_threads():
    """
    Wait for all active job threads to complete, up to the timeout.

    Logs which threads finished and which timed out.
    """
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


# --- Main Application Logic ---
def main():
    """
    The main operational loop for the worker agent.

    This function continuously attempts to register with the manager and, once
    successful, enters a loop to send heartbeats and poll for new jobs. The loop
    checks a shutdown event each cycle and exits gracefully when signaled,
    waiting for active job threads to complete.
    """
    # Install signal handlers for graceful shutdown.
    signal.signal(signal.SIGINT, _shutdown_handler)
    signal.signal(signal.SIGTERM, _shutdown_handler)

    logger.info("Sethlans Reborn Worker Agent Starting...")

    worker_id = None

    while not _shutdown_event.is_set():
        try:
            # Prune completed threads each cycle.
            _prune_finished_threads()

            if not worker_id:
                logger.warning("Worker not registered with Manager. Attempting registration...")
                new_id = system_monitor.register_with_manager()
                if new_id:
                    worker_id = new_id
                    # Start web UI after registration (A-NF9)
                    start_server()
                else:
                    logger.error("Failed to register with manager. Retrying in 30 seconds...")
                    _shutdown_event.wait(30)
                    continue

            # Heartbeats continue regardless of pause state.
            system_monitor.send_heartbeat()

            # Skip job polling when paused (A-NF7).
            if job_processor.is_paused():
                logger.debug("Worker is paused. Skipping job poll.")
                _shutdown_event.wait(config.JOB_POLLING_INTERVAL_SECONDS)
                continue

            thread = job_processor.get_and_claim_job(worker_id)
            if thread is not None:
                with _active_threads_lock:
                    _active_threads.append(thread)

            logger.debug(
                f"Loop finished. Sleeping for {config.JOB_POLLING_INTERVAL_SECONDS} seconds."
            )
            _shutdown_event.wait(config.JOB_POLLING_INTERVAL_SECONDS)

        except Exception as e:
            logger.critical(
                f"An unhandled exception occurred in the main loop: {e}",
                exc_info=True
            )
            logger.info("Restarting main loop in 60 seconds...")
            _shutdown_event.wait(60)

    # --- Graceful shutdown sequence ---
    logger.info("Shutdown event set. Stopping polling loop.")
    stop_server()
    _wait_for_active_threads()
    logger.info("Sethlans Reborn Worker Agent shut down cleanly.")


if __name__ == '__main__':
    main()
