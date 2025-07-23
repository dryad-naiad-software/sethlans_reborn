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

import requests
import json
import time
import platform
import socket
import os
import sys
import subprocess  # For executing Blender
import datetime  # For timestamps in reports

# --- Configuration ---
# Base URL of your Sethlans Reborn Manager's API endpoints
# Ensure your Django development server is running at this address!
MANAGER_API_URL = "http://127.0.0.1:8000/api/"

# Intervals for worker operations
HEARTBEAT_INTERVAL_SECONDS = 30  # How often the worker sends a heartbeat to update its 'last_seen'
JOB_POLLING_INTERVAL_SECONDS = 5  # How often the worker checks for new jobs

# Calculate the main project root from the worker agent's script location.
# agent.py is in sethlans_reborn/sethlans_worker_agent/
# os.path.dirname(os.path.abspath(sys.argv[0])) gives C:\Users\mestrella\Projects\sethlans_reborn\sethlans_worker_agent\
# os.path.join(..., '..') moves up to C:\Users\mestrella\Projects\sethlans_reborn\
PROJECT_ROOT_FOR_WORKER = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), '..'))

# IMPORTANT: Path to your Blender executable on THIS worker machine
BLENDER_EXECUTABLE = r"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe"

# Placeholder paths for testing blend files/output. In a real scenario, these would come from the job data.
TEST_BLEND_FILE_PATH = os.path.join(PROJECT_ROOT_FOR_WORKER, 'test_scene.blend')
TEST_OUTPUT_DIR = os.path.join(PROJECT_ROOT_FOR_WORKER, 'render_test_output')

# Global variable to store worker's own info once registered with the manager
# This is used to identify the worker when claiming jobs.
WORKER_INFO = {}


def get_system_info():
    """Gathers basic system information for the heartbeat."""
    hostname = socket.gethostname()
    ip_address = None
    try:
        ip_address = socket.gethostbyname(hostname)  # Gets primary IPv4 address
    except socket.gaierror:
        pass  # Could not resolve hostname

    os_info = platform.system()  # e.g., 'Windows', 'Linux', 'Darwin'
    # Add more detailed OS info if needed (e.g., specific Windows version, Linux kernel)
    if os_info == 'Windows':
        os_info += f" {platform.release()}"
    elif os_info == 'Linux':
        os_info += f" {platform.version()}"

    # This can be expanded in Phase 3.1 to include CPU, GPU, RAM, etc.
    return {
        "hostname": hostname,
        "ip_address": ip_address,
        "os": os_info
    }


def send_heartbeat(system_info):
    """
    Sends a heartbeat to the Django Manager's heartbeat API endpoint.
    Updates the global WORKER_INFO with the worker's ID received from the manager.
    """
    global WORKER_INFO  # Declare intent to modify global variable
    heartbeat_url = f"{MANAGER_API_URL}heartbeat/"
    try:
        print(f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Sending heartbeat to {heartbeat_url}...")
        response = requests.post(heartbeat_url, json=system_info, timeout=5)
        response.raise_for_status()  # Raise an exception for HTTP errors (4xx or 5xx)

        response_data = response.json()
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Heartbeat successful: HTTP {response.status_code}")
        # Store the worker's ID and hostname from the manager's response
        WORKER_INFO = response_data
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Worker registered as ID: {WORKER_INFO.get('id')}, Hostname: {WORKER_INFO.get('hostname')}")

    except requests.exceptions.Timeout:
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: Heartbeat timed out after 5 seconds.")
    except requests.exceptions.RequestException as e:
        print(f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: Heartbeat failed - {e}")
    except json.JSONDecodeError:
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: Failed to decode JSON response from heartbeat.")
    except Exception as e:
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] An unexpected error occurred during heartbeat: {e}")


def execute_blender_job(job_data):
    """
    Executes a Blender render job using subprocess.
    Args:
        job_data (dict): Dictionary containing job details like blend_file_path, output_file_pattern, etc.
    Returns:
        tuple: (success: bool, stdout: str, stderr: str, error_message: str)
    """
    job_name = job_data.get('name', 'Unnamed Job')
    blend_file_path = job_data.get('blend_file_path')
    output_file_pattern = job_data.get('output_file_pattern')
    start_frame = job_data.get('start_frame', 1)
    end_frame = job_data.get('end_frame', 1)
    blender_version_req = job_data.get('blender_version', 'latest')
    render_engine = job_data.get('render_engine', 'CYCLES')

    print(f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Starting render for job '{job_name}'...")
    print(f"  Blend File: {blend_file_path}")
    print(f"  Output Pattern: {output_file_pattern}")
    print(f"  Frames: {start_frame}-{end_frame}")
    print(f"  Engine: {render_engine}")

    # Ensure output directory exists before rendering
    # Extract just the directory part from the output_file_pattern
    output_dir = os.path.dirname(output_file_pattern)
    if not os.path.exists(output_dir):
        try:
            os.makedirs(output_dir)
            print(
                f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Created output directory: {output_dir}")
        except OSError as e:
            err_msg = f"Failed to create output directory {output_dir}: {e}"
            print(f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: {err_msg}")
            return False, "", "", err_msg

    command = [
        BLENDER_EXECUTABLE,
        "-b",  # Run in background (headless)
        blend_file_path,  # Path to the .blend file
        # Blender expects single # for pattern when rendering animation/multiple frames
        # and it will replace with actual frame number and add extension
        "-o", output_file_pattern.replace('####', '#'),
        "-F", "PNG",  # Default output format for now (can be made configurable via job data)
        "-E", render_engine,  # Render engine
    ]

    # Add frame arguments based on single frame or range
    if start_frame == end_frame:
        command.extend(["-f", str(start_frame)])  # Render a single frame
    else:
        # Render an animation sequence
        command.extend(["-s", str(start_frame), "-e", str(end_frame), "-a"])

    print(
        f"\n[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Running Blender command: {' '.join(command)}")

    stdout_output = ""
    stderr_output = ""
    error_message = ""
    success = False

    try:
        # Execute the command
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,  # Decode stdout/stderr as text
            check=False,  # Do not raise exception for non-zero exit code immediately, we handle it below
            cwd=PROJECT_ROOT_FOR_WORKER  # Set current working directory for Blender
        )

        stdout_output = process.stdout
        stderr_output = process.stderr

        if process.returncode == 0:
            print(
                f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Blender render command exited successfully.")
            success = True
        else:
            error_message = f"Blender exited with code {process.returncode}. STDERR: {stderr_output[:500]}..."
            print(
                f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: Blender command failed: {error_message}")
            success = False

        print("--- Blender STDOUT (last 1000 chars) ---")
        print(stdout_output[-1000:])  # Print last 1000 chars of stdout for brevity
        print("--- Blender STDERR (last 1000 chars) ---")
        print(stderr_output[-1000:])  # Print last 1000 chars of stderr

    except FileNotFoundError:
        error_message = f"Blender executable not found at '{BLENDER_EXECUTABLE}'. Please check the path."
        print(f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: {error_message}")
    except Exception as e:
        error_message = f"An unexpected error occurred during Blender execution: {e}"
        print(f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: {error_message}")

    return success, stdout_output, stderr_output, error_message


def get_and_claim_job():
    """Polls the manager for available jobs and attempts to claim one. If claimed, executes the job."""
    if not WORKER_INFO.get('id'):
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Worker ID not yet known. Skipping job poll.")
        return

    jobs_url = f"{MANAGER_API_URL}jobs/"
    try:
        print(f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Polling for jobs from {jobs_url}...")
        # Get only QUEUED jobs that are not assigned to any worker (assigned_worker is null)
        response = requests.get(jobs_url, params={'status': 'QUEUED', 'assigned_worker__isnull': 'true'}, timeout=10)
        response.raise_for_status()
        available_jobs = response.json()

        if available_jobs:
            print(
                f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Found {len(available_jobs)} available job(s).")
            # For simplicity, pick the first job in the list
            job_to_claim = available_jobs[0]
            job_id = job_to_claim.get('id')
            job_name = job_to_claim.get('name')

            print(
                f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Attempting to claim job '{job_name}' (ID: {job_id})...")

            # Send a PATCH request to the manager to claim the job
            claim_url = f"{jobs_url}{job_id}/"
            claim_payload = {
                "status": "RENDERING",
                "assigned_worker": WORKER_INFO['id']  # Assign worker by its ID
            }
            claim_response = requests.patch(claim_url, json=claim_payload, timeout=5)
            claim_response.raise_for_status()

            # Verify that the claim was successful from the manager's response
            claimed_job_data = claim_response.json()
            if claimed_job_data.get('status') == 'RENDERING' and claimed_job_data.get('assigned_worker') == WORKER_INFO[
                'id']:
                print(
                    f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Successfully claimed job '{job_name}'! Starting render...")

                # --- EXECUTE BLENDER JOB ---
                success, stdout, stderr, blender_error_msg = execute_blender_job(job_to_claim)

                # --- REPORT JOB STATUS BACK TO MANAGER ---
                job_update_payload = {
                    "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z'),
                    # ISO 8601 UTC format
                    "last_output": stdout,
                    "error_message": blender_error_msg if blender_error_msg else stderr,
                    # Prioritize explicit error, fallback to stderr
                }

                if success:
                    job_update_payload["status"] = "DONE"
                else:
                    job_update_payload["status"] = "ERROR"

                try:
                    print(
                        f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Reporting job '{job_name}' status '{job_update_payload['status']}' back to manager...")
                    report_response = requests.patch(claim_url, json=job_update_payload, timeout=5)
                    report_response.raise_for_status()
                    print(
                        f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Job '{job_name}' status report successful.")
                except requests.exceptions.RequestException as e:
                    print(
                        f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: Failed to report job status for '{job_name}' - {e}")

                return job_to_claim  # Return the processed job
            else:
                print(
                    f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: Claim failed or manager response was unexpected. Job status: {claimed_job_data.get('status')}. Worker assigned: {claimed_job_data.get('assigned_worker')}")
        else:
            print(f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] No QUEUED jobs available.")

    except requests.exceptions.Timeout:
        print(f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: Job polling or claiming timed out.")
    except requests.exceptions.RequestException as e:
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: Job polling or claiming failed - {e}")
    except json.JSONDecodeError:
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: Failed to decode JSON response from job API.")
    except Exception as e:
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] An unexpected error occurred during job polling/claiming: {e}")


if __name__ == "__main__":
    print("Sethlans Reborn Worker Agent Starting...")
    # Ensure the Django Manager (sethlans_reborn project) is running at http://127.0.0.1:8000/!

    # Initial system info for the first heartbeat
    initial_system_info = get_system_info()

    while True:
        # Send heartbeat. Use full system info for initial registration, then just hostname for updates.
        if not WORKER_INFO:
            send_heartbeat(initial_system_info)
        else:
            # For subsequent heartbeats, only need to send enough to update 'last_seen'
            # The manager recognizes the worker by hostname and updates its record.
            send_heartbeat({'hostname': WORKER_INFO['hostname']})

        # After sending heartbeat (and getting ID), poll for jobs and execute if found
        get_and_claim_job()

        # Sleep for the minimum of heartbeat and job polling intervals to keep responsive
        time.sleep(min(HEARTBEAT_INTERVAL_SECONDS, JOB_POLLING_INTERVAL_SECONDS))