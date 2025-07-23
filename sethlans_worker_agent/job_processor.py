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
import datetime
import time
import subprocess
import os

from . import config
from . import system_monitor
from .tool_manager import tool_manager_instance  # <-- NEW IMPORT: Import the instance


# --- Job Processing Functions ---

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
    blender_version_req = job_data.get('blender_version')
    render_engine = job_data.get('render_engine', 'CYCLES')

    print(f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Starting render for job '{job_name}'...")
    print(f"  Blend File: {blend_file_path}")
    print(f"  Output Pattern: {output_file_pattern}")
    print(f"  Frames: {start_frame}-{end_frame}")
    print(f"  Engine: {render_engine}")
    if blender_version_req:
        print(f"  Requested Blender Version: {blender_version_req}")

    # --- Determine which Blender executable to use ---
    blender_to_use = config.SYSTEM_BLENDER_EXECUTABLE  # Default to system-wide Blender from config
    if blender_version_req:
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Attempting to ensure Blender version {blender_version_req} is available...")
        # CORRECTED CALL: Use tool_manager_instance.ensure_blender_version_available
        managed_blender_path = tool_manager_instance.ensure_blender_version_available(blender_version_req)
        if managed_blender_path:
            blender_to_use = managed_blender_path
            print(
                f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Using managed Blender version from: {blender_to_use}")
        else:
            print(
                f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] WARNING: Requested Blender version {blender_version_req} not available/downloadable. Falling back to default system Blender: {blender_to_use}")

    # Ensure output directory exists before rendering
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
        blender_to_use,
        "-b",
        blend_file_path,
        "-o", output_file_pattern.replace('####', '#'),
        "-F", "PNG",
        "-E", render_engine,
    ]

    if start_frame == end_frame:
        command.extend(["-f", str(start_frame)])
    else:
        command.extend(["-s", str(start_frame), "-e", str(end_frame), "-a"])

    print(
        f"\n[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Running Blender command: {' '.join(command)}")

    stdout_output = ""
    stderr_output = ""
    error_message = ""
    success = False

    try:
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            cwd=config.PROJECT_ROOT_FOR_WORKER
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
        print(stdout_output[-1000:])
        print("--- Blender STDERR (last 1000 chars) ---")
        print(stderr_output[-1000:])

    except FileNotFoundError:
        error_message = f"Blender executable not found at '{blender_to_use}'. Please check the path/download."
        print(f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: {error_message}")
    except Exception as e:
        error_message = f"An unexpected error occurred during Blender execution: {e}"
        print(f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: {e}")

    return success, stdout_output, stderr_output, error_message


def get_and_claim_job():
    """Polls the manager for available jobs and attempts to claim one. If claimed, executes the job."""
    if not system_monitor.WORKER_INFO.get('id'):
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Worker ID not yet known. Skipping job poll.")
        return

    jobs_url = f"{config.MANAGER_API_URL}jobs/"
    try:
        print(f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Polling for jobs from {jobs_url}...")
        response = requests.get(jobs_url, params={'status': 'QUEUED', 'assigned_worker__isnull': 'true'}, timeout=10)
        response.raise_for_status()
        available_jobs = response.json()

        if available_jobs:
            print(
                f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Found {len(available_jobs)} available job(s).")
            job_to_claim = available_jobs[0]
            job_id = job_to_claim.get('id')
            job_name = job_to_claim.get('name')

            print(
                f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Attempting to claim job '{job_name}' (ID: {job_id})...")

            # Send a PATCH request to the manager to claim the job
            claim_url = f"{jobs_url}{job_id}/"
            claim_payload = {
                "status": "RENDERING",
                "assigned_worker": system_monitor.WORKER_INFO['id']
            }
            claim_response = requests.patch(claim_url, json=claim_payload, timeout=5)
            claim_response.raise_for_status()

            # Verify that the claim was successful from the manager's response
            claimed_job_data = claim_response.json()
            if claimed_job_data.get('status') == 'RENDERING' and claimed_job_data.get('assigned_worker') == \
                    system_monitor.WORKER_INFO['id']:
                print(
                    f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Successfully claimed job '{job_name}'! Starting render...")

                # --- EXECUTE BLENDER JOB ---
                success, stdout, stderr, blender_error_msg = execute_blender_job(job_to_claim)

                # --- REPORT JOB STATUS BACK TO MANAGER ---
                job_update_payload = {
                    "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z'),
                    "last_output": stdout,
                    "error_message": blender_error_msg if blender_error_msg else stderr,
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

                return job_to_claim
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