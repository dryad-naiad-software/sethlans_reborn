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
import time
import logging

# Import the new modules
from . import config
from . import system_monitor
from . import job_processor

# Ensure logging is configured (calls the function in config.py)
# config.configure_worker_logging(logging.DEBUG) # You can override level here for debug runs

# Get a logger for this module
logger = logging.getLogger(__name__)

# Global variable to store worker's own info once registered
WORKER_INFO = {}

if __name__ == "__main__":
    # This initial print is for immediate visibility before logging takes over fully
    print("Sethlans Reborn Worker Agent Starting...")
    logger.info("Worker Agent starting main loop...")

    # The main loop will now handle registration and polling
    while True:
        # Check if the worker is registered (i.e., has an ID from the manager)
        if not system_monitor.WORKER_INFO.get('id'):
            logger.warning("Worker not registered with Manager. Attempting registration heartbeat...")

            # Re-gather full system info for a registration attempt
            full_system_info = system_monitor.get_system_info()
            system_monitor.send_heartbeat(full_system_info)

            # If registration fails, we'll sleep and try again in the next loop iteration
            if not system_monitor.WORKER_INFO.get('id'):
                logger.error("Registration failed. Will retry after a delay.")
        else:
            # If we are registered, perform normal duties
            logger.info(f"Worker registered as ID {system_monitor.WORKER_INFO['id']}. Polling for jobs...")

            # Send a simple, lightweight heartbeat
            system_monitor.send_heartbeat({'hostname': system_monitor.WORKER_INFO['hostname']})

            # Poll for and process jobs
            job_processor.get_and_claim_job()

        # Sleep at the end of every loop iteration to prevent spamming the manager
        logger.debug(f"Loop finished. Sleeping for {config.JOB_POLLING_INTERVAL_SECONDS} seconds.")
        time.sleep(config.JOB_POLLING_INTERVAL_SECONDS)