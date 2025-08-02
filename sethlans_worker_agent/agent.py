# FILENAME: sethlans_worker_agent/agent.py
# sethlans_worker_agent/agent.py

#
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
#
#
# Created by Mario Estrella on 07/22/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
"""
The main entry point for the Sethlans Reborn Worker Agent.

This script initializes the worker, handles command-line arguments, and
enters an infinite loop to perform its core duties:
1. Registering with the central manager.
2. Sending periodic heartbeats to maintain a live connection.
3. Polling the manager for new render jobs.
4. Claiming and executing available jobs.

The agent's behavior and logging level can be configured via command-line arguments.
"""

import argparse
import logging
from logging.handlers import RotatingFileHandler
import time
import sys
from sethlans_worker_agent import job_processor, system_monitor, config

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
    maxBytes=5*1024*1024, # 5 MB
    backupCount=3
)
file_handler.setFormatter(formatter)
root_logger.addHandler(file_handler)

# Get the logger for this module specifically
logger = logging.getLogger(__name__)


# --- Main Application Logic ---
def main():
    """
    The main operational loop for the worker agent.

    This function continuously attempts to register with the manager and, once
    successful, enters a loop to send heartbeats and poll for new jobs. The loop
    is designed to be resilient to temporary network failures and handles graceful
    shutdowns via a KeyboardInterrupt.
    """
    logger.info("Sethlans Reborn Worker Agent Starting...")

    worker_id = None

    while True:
        try:
            if not worker_id:
                logger.warning("Worker not registered with Manager. Attempting registration...")
                new_id = system_monitor.register_with_manager()
                if new_id:
                    worker_id = new_id
                else:
                    logger.error("Failed to register with manager. Retrying in 30 seconds...")
                    time.sleep(30)
                    continue

            # If registered, perform regular heartbeat and check for jobs.
            system_monitor.send_heartbeat()
            job_processor.get_and_claim_job(worker_id)

            # --- RESTORED: Always sleep after a work cycle ---
            logger.debug(f"Loop finished. Sleeping for {config.JOB_POLLING_INTERVAL_SECONDS} seconds.")
            time.sleep(config.JOB_POLLING_INTERVAL_SECONDS)

        except KeyboardInterrupt:
            logger.info("Shutdown signal received. Exiting...")
            sys.exit(0)
        except Exception as e:
            logger.critical(f"An unhandled exception occurred in the main loop: {e}", exc_info=True)
            logger.info("Restarting main loop in 60 seconds...")
            time.sleep(60)


if __name__ == '__main__':
    main()