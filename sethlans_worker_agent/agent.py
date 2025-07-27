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

import argparse
import logging
import time
import sys
from sethlans_worker_agent import job_processor, system_monitor, config

# --- Logging Setup ---
LOG_LEVELS = {
    'DEBUG': logging.DEBUG,
    'INFO': logging.INFO,
    'WARNING': logging.WARNING,
    'ERROR': logging.ERROR,
    'CRITICAL': logging.CRITICAL
}
parser = argparse.ArgumentParser(description="Sethlans Reborn Worker Agent")
parser.add_argument(
    '--loglevel',
    dest='loglevel',
    choices=LOG_LEVELS.keys(),
    default='INFO',
    help='Set the logging level'
)
args = parser.parse_args()

logging.basicConfig(
    level=LOG_LEVELS[args.loglevel],
    format='[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


# --- Main Application Logic ---
def main():
    """Main loop for the worker agent."""
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