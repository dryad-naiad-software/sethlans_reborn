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
# Created by Mario Estrella on 7/22/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
import requests
import json
import time
import platform
import socket
import os
import sys

# --- Configuration ---
MANAGER_API_URL = "http://127.0.0.1:8000/api/"  # Base API URL
HEARTBEAT_INTERVAL_SECONDS = 30
JOB_POLLING_INTERVAL_SECONDS = 5  # How often the worker checks for new jobs

PROJECT_ROOT_FOR_WORKER = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), '..'))
BLENDER_EXECUTABLE = r"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe"
TEST_BLEND_FILE_PATH = os.path.join(PROJECT_ROOT_FOR_WORKER, 'test_scene.blend')
TEST_OUTPUT_DIR = os.path.join(PROJECT_ROOT_FOR_WORKER, 'render_test_output')

# Global variable to store worker's own info once registered
# This will be used to claim jobs.
WORKER_INFO = {}


def get_system_info():
    """Gathers basic system information for the heartbeat."""
    hostname = socket.gethostname()
    ip_address = None
    try:
        ip_address = socket.gethostbyname(hostname)
    except socket.gaierror:
        pass

    os_info = platform.system()
    if os_info == 'Windows':
        os_info += f" {platform.release()}"
    elif os_info == 'Linux':
        os_info += f" {platform.version()}"

    return {
        "hostname": hostname,
        "ip_address": ip_address,
        "os": os_info
    }


def send_heartbeat(system_info):
    """Sends a heartbeat to the Django Manager and updates WORKER_INFO."""
    global WORKER_INFO  # Declare intent to modify global variable
    heartbeat_url = f"{MANAGER_API_URL}heartbeat/"
    try:
        print(f"[{time.ctime()}] Sending heartbeat to {heartbeat_url}...")
        response = requests.post(heartbeat_url, json=system_info, timeout=5)
        response.raise_for_status()

        response_data = response.json()
        print(f"[{time.ctime()}] Heartbeat successful: HTTP {response.status_code}")
        # Store the worker's ID from the manager's response
        WORKER_INFO = response_data
        print(
            f"[{time.ctime()}] Worker registered as ID: {WORKER_INFO.get('id')}, Hostname: {WORKER_INFO.get('hostname')}")

    except requests.exceptions.Timeout:
        print(f"[{time.ctime()}] ERROR: Heartbeat timed out after 5 seconds.")
    except requests.exceptions.RequestException as e:
        print(f"[{time.ctime()}] ERROR: Heartbeat failed - {e}")
    except json.JSONDecodeError:
        print(f"[{time.ctime()}] ERROR: Failed to decode JSON response from heartbeat.")
    except Exception as e:
        print(f"[{time.ctime()}] An unexpected error occurred during heartbeat: {e}")


def get_and_claim_job():
    """Polls the manager for available jobs and attempts to claim one."""
    if not WORKER_INFO.get('id'):
        print(f"[{time.ctime()}] Worker ID not yet known. Skipping job poll.")
        return

    jobs_url = f"{MANAGER_API_URL}jobs/"
    try:
        print(f"[{time.ctime()}] Polling for jobs from {jobs_url}...")
        # Get only QUEUED jobs that are not assigned to any worker
        # We'll modify the Django API to filter these requests.
        response = requests.get(jobs_url, params={'status': 'QUEUED', 'assigned_worker__isnull': 'true'}, timeout=10)
        response.raise_for_status()
        available_jobs = response.json()

        if available_jobs:
            print(f"[{time.ctime()}] Found {len(available_jobs)} available job(s).")
            # For simplicity, pick the first job
            job_to_claim = available_jobs[0]
            job_id = job_to_claim.get('id')
            job_name = job_to_claim.get('name')

            print(f"[{time.ctime()}] Attempting to claim job '{job_name}' (ID: {job_id})...")

            # Send a PATCH request to claim the job
            claim_url = f"{jobs_url}{job_id}/"
            claim_payload = {
                "status": "RENDERING",
                "assigned_worker": WORKER_INFO['id']  # Assign worker by its ID
            }
            claim_response = requests.patch(claim_url, json=claim_payload, timeout=5)
            claim_response.raise_for_status()

            print(f"[{time.ctime()}] Successfully claimed job '{job_name}'! Starting render...")
            # Here you would typically initiate the Blender render process
            # For now, we'll just print a message.
            # You would pass job_to_claim details to a render function here.

            return job_to_claim  # Return the claimed job for further processing

        else:
            print(f"[{time.ctime()}] No QUEUED jobs available.")

    except requests.exceptions.Timeout:
        print(f"[{time.ctime()}] ERROR: Job polling or claiming timed out.")
    except requests.exceptions.RequestException as e:
        print(f"[{time.ctime()}] ERROR: Job polling or claiming failed - {e}")
    except json.JSONDecodeError:
        print(f"[{time.ctime()}] ERROR: Failed to decode JSON response from job API.")
    except Exception as e:
        print(f"[{time.ctime()}] An unexpected error occurred during job polling/claiming: {e}")


if __name__ == "__main__":
    print("Sethlans Reborn Worker Agent Starting...")
    # Ensure the Django Manager (sethlans_reborn project) is running!

    while True:
        # First, send heartbeat to ensure worker is registered and get its ID
        if not WORKER_INFO:  # Only send full system info if we don't have worker_info yet
            send_heartbeat(get_system_info())
        else:  # For subsequent heartbeats, just send basic ID/hostname to update last_seen
            send_heartbeat({'hostname': WORKER_INFO['hostname']})  # Only send hostname for update

        # Then, poll for jobs
        get_and_claim_job()

        # Adjust sleep to balance heartbeat and polling,
        # or have a separate thread for polling
        time.sleep(min(HEARTBEAT_INTERVAL_SECONDS, JOB_POLLING_INTERVAL_SECONDS))