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
# sethlans_worker_agent/agent.py

import time
import logging
import argparse  # <-- ADD THIS IMPORT

from . import config
from . import system_monitor
from . import job_processor

# Get a logger for this module
logger = logging.getLogger(__name__)

# Global variable to store worker's own info once registered
WORKER_INFO = {}

if __name__ == "__main__":
    # --- START OF MODIFIED BLOCK ---
    # 1. Set up argument parser
    parser = argparse.ArgumentParser(description="Sethlans Reborn Worker Agent")
    parser.add_argument(
        "--loglevel",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Set the logging level for the worker agent."
    )
    args = parser.parse_args()

    # 2. Configure logging using the parsed argument
    config.configure_worker_logging(args.loglevel)
    # --- END OF MODIFIED BLOCK ---

    print("Sethlans Reborn Worker Agent Starting...")
    logger.info("Worker Agent starting main loop...")

    # The main loop will now handle registration and polling
    while True:
        if not system_monitor.WORKER_INFO.get('id'):
            logger.warning("Worker not registered with Manager. Attempting registration heartbeat...")

            full_system_info = system_monitor.get_system_info()
            system_monitor.send_heartbeat(full_system_info)

            if not system_monitor.WORKER_INFO.get('id'):
                logger.error("Registration failed. Will retry after a delay.")
        else:
            logger.info(f"Worker registered as ID {system_monitor.WORKER_INFO['id']}. Polling for jobs...")

            system_monitor.send_heartbeat({'hostname': system_monitor.WORKER_INFO['hostname']})
            job_processor.get_and_claim_job()

        logger.debug(f"Loop finished. Sleeping for {config.JOB_POLLING_INTERVAL_SECONDS} seconds.")
        time.sleep(config.JOB_POLLING_INTERVAL_SECONDS)