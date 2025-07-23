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
import logging  # <-- NEW IMPORT

# Import the new modules
from . import config
from . import system_monitor
from . import job_processor

# Get a logger for this module
logger = logging.getLogger(__name__)

# Global variable to store worker's own info once registered
# This object is managed by system_monitor.py but accessed via module.
WORKER_INFO = {}

if __name__ == "__main__":
    # This initial print is for immediate visibility before logging takes over fully
    print("Sethlans Reborn Worker Agent Starting...")
    # Ensure the Django Manager (sethlans_reborn project) is running at http://127.0.0.1:8000/!

    # Initial system info for the first heartbeat
    initial_system_info = system_monitor.get_system_info()

    # Send initial heartbeat to ensure worker is registered and get its ID
    # This populates system_monitor.WORKER_INFO
    system_monitor.send_heartbeat(initial_system_info)

    # --- Explicit READY message ---
    # This signals that the worker has performed its initial setup before polling for jobs.
    logger.info("Worker Agent READY.")

    while True:
        # Send subsequent heartbeats using only hostname for efficiency
        system_monitor.send_heartbeat({'hostname': system_monitor.WORKER_INFO['hostname']})

        # Job polling and processing
        job_processor.get_and_claim_job()

        # Sleep for the minimum of heartbeat and job polling intervals to keep responsive
        time.sleep(min(config.HEARTBEAT_INTERVAL_SECONDS, config.JOB_POLLING_INTERVAL_SECONDS))