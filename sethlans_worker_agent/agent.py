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

# Import the new modules
from . import config
from . import system_monitor
from . import job_processor

if __name__ == "__main__":
    print("Sethlans Reborn Worker Agent Starting...")
    # Ensure the Django Manager (sethlans_reborn project) is running at http://127.0.0.1:8000/!

    # Initial system info for the first heartbeat
    # This call also triggers initial scan/generation of Blender info through system_monitor -> tool_manager
    initial_system_info = system_monitor.get_system_info()

    while True:
        # Send heartbeat. Use full system info for initial registration, then just hostname for updates.
        if not system_monitor.WORKER_INFO:  # Check WORKER_INFO from system_monitor module
            system_monitor.send_heartbeat(initial_system_info)
        else:
            # For subsequent heartbeats, only need to send enough to update 'last_seen'
            # The manager recognizes the worker by hostname and updates its record.
            system_monitor.send_heartbeat({'hostname': system_monitor.WORKER_INFO['hostname']})

        # Job polling and processing
        job_processor.get_and_claim_job()

        # Sleep for the minimum of heartbeat and job polling intervals to keep responsive
        time.sleep(min(config.HEARTBEAT_INTERVAL_SECONDS, config.JOB_POLLING_INTERVAL_SECONDS))