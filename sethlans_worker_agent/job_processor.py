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
# Created by Mario Estrella on 07/23/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
# sethlans_worker_agent/job_processor.py

import datetime
import logging
import os
import requests
import subprocess
import time
import platform
import threading
import psutil
import tempfile

from sethlans_worker_agent import config
from sethlans_worker_agent.tool_manager import tool_manager_instance

logger = logging.getLogger(__name__)


def update_job_status(job_url, payload):
    """Updates the job status via the API."""
    try:
        response = requests.patch(job_url, json=payload, timeout=5)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to update job status at {job_url}: {e}")


def get_and_claim_job(worker_id):
    """Polls the manager for a job, claims it, and processes it."""
    poll_url = f"{config.MANAGER_API_URL}jobs/"
    params = {'status': 'QUEUED', 'assigned_worker__isnull': 'true'}

    try:
        response = requests.get(poll_url, params=params, timeout=10)
        response.raise_for_status()
        available_jobs = response.json()

        if available_jobs:
            job_to_claim = available_jobs[0]
            job_id = job_to_claim.get('id')
            job_name = job_to_claim.get('name', 'Unnamed Job')
            claim_url = f"{config.MANAGER_API_URL}jobs/{job_id}/"

            logger.info(f"Found {len(available_jobs)} available job(s).")
            logger.info(f"Attempting to claim job '{job_name}' (ID: {job_id})...")

            claim_response = requests.patch(claim_url, json={"assigned_worker": worker_id}, timeout=5)

            if claim_response.status_code == 200:
                logger.info(f"Successfully claimed job '{job_name}'! Starting render...")
                update_job_status(claim_url, {"status": "RENDERING"})

                success, was_canceled, stdout, stderr, blender_error_msg = execute_blender_job(job_to_claim)

                job_update_payload = {
                    "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z'),
                    "last_output": stdout,
                    "error_message": blender_error_msg,
                }

                if success:
                    job_update_payload["status"] = "DONE"
                elif was_canceled:
                    job_update_payload["status"] = "CANCELED"
                else:
                    job_update_payload["status"] = "ERROR"

                report_response = requests.patch(claim_url, json=job_update_payload, timeout=5)

                if report_response.status_code == 200:
                    logger.info(
                        f"Successfully reported final status '{job_update_payload['status']}' for job {job_id}.")
                else:
                    logger.error(
                        f"Failed to report final status for job {job_id}. Server responded with {report_response.status_code}.")

            elif claim_response.status_code == 409:
                logger.warning(f"Job {job_id} was claimed by another worker. Looking for another job.")
            else:
                logger.error(
                    f"Failed to claim job {job_id}. Status: {claim_response.status_code}, Response: {claim_response.text}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Could not poll for jobs: {e}")


def _stream_reader(stream, output_list):
    """Helper function to read a stream line by line into a list."""
    try:
        for line in iter(stream.readline, ''):
            output_list.append(line)
    finally:
        stream.close()


def execute_blender_job(job_data):
    """
    Executes a Blender render job, monitoring for cancellation and reading output via threads.
    Returns (success: bool, was_canceled: bool, stdout: str, stderr: str, error_message: str)
    """
    job_id = job_data.get('id')
    job_name = job_data.get('name', 'Unnamed Job')
    blend_file_path = job_data.get('blend_file_path')
    output_file_pattern = job_data.get('output_file_pattern')
    start_frame = job_data.get('start_frame', 1)
    end_frame = job_data.get('end_frame', 1)
    blender_version_req = job_data.get('blender_version')
    render_engine = job_data.get('render_engine', 'CYCLES')
    render_settings = job_data.get('render_settings', {})
    render_device = job_data.get('render_device', 'CPU')
    temp_script_path = None # Keep for render_settings, but not used for GPU

    logger.info(f"Starting render for job '{job_name}' (ID: {job_id})...")

    blender_to_use = tool_manager_instance.ensure_blender_version_available(
        blender_version_req) if blender_version_req else config.SYSTEM_BLENDER_EXECUTABLE
    if not blender_to_use:
        error_message = f"Could not find or acquire Blender version '{blender_version_req}'. Aborting job."
        return False, False, "", "", error_message

    logger.info(f"Using Blender executable: {blender_to_use}")

    output_dir = os.path.dirname(output_file_pattern)
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # --- FINAL REVISED LOGIC: Conditional --factory-startup ---
    command = [
        blender_to_use, "-b", blend_file_path,
        "-o", output_file_pattern.replace('####', '#'),
        "-F", "PNG", "-E", render_engine,
    ]

    # For CPU jobs, force a clean state to override any .blend file GPU settings.
    # For GPU jobs, omit it to RESPECT the .blend file's GPU settings.
    if render_device == 'CPU':
        command.insert(1, "--factory-startup")
        logger.info("CPU job detected. Using --factory-startup to ensure CPU rendering.")
    else:
        logger.info("GPU job detected. Omitting --factory-startup to use pre-configured .blend file settings.")

    if start_frame == end_frame:
        command.extend(["-f", str(start_frame)])
    else:
        command.extend(["-s", str(start_frame), "-e", str(end_frame), "-a"])

    # This override is kept for applying render settings from the API, if any.
    if isinstance(render_settings, dict):
        for key_path, value in render_settings.items():
            py_value = f"'{value}'" if isinstance(value, str) else value
            py_command = f"import bpy; bpy.context.scene.{key_path} = {py_value}"
            command.extend(["--python-expr", py_command])

    logger.info(f"Running Command: {' '.join(command)}")

    process = None
    success = False
    stdout_lines, stderr_lines, error_message = [], [], ""
    stdout_output, stderr_output = "", ""
    was_canceled = False
    final_return_code = -1

    try:
        popen_kwargs = {
            "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
            "encoding": 'utf-8', "errors": 'surrogateescape',
            "cwd": config.PROJECT_ROOT_FOR_WORKER
        }
        if platform.system() == "Windows":
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        process = subprocess.Popen(command, **popen_kwargs)

        stdout_thread = threading.Thread(target=_stream_reader, args=(process.stdout, stdout_lines))
        stderr_thread = threading.Thread(target=_stream_reader, args=(process.stderr, stderr_lines))
        stdout_thread.start()
        stderr_thread.start()

        job_url = f"{config.MANAGER_API_URL}jobs/{job_id}/"

        while psutil.pid_exists(process.pid):
            try:
                response = requests.get(job_url, timeout=5)
                if response.status_code == 200 and response.json().get('status') == 'CANCELED':
                    logger.warning(f"Cancellation signal received for job ID {job_id}. Killing Blender process...")
                    process.kill()
                    was_canceled = True
                    break
            except requests.exceptions.RequestException as e:
                logger.error(f"API error while checking for cancellation: {e}")
            time.sleep(2)

        stdout_thread.join()
        stderr_thread.join()
        final_return_code = process.wait()

    except Exception as e:
        error_message = f"An unexpected error occurred during Blender execution: {e}"
        success = False
        was_canceled = False
        final_return_code = -1

    stdout_output = "".join(stdout_lines)
    stderr_output = "".join(stderr_lines)

    if was_canceled:
        error_message = "Job was canceled by user request."
        success = False
    elif final_return_code == 0:
        logger.info("Render command completed successfully.")
        error_message = ""
        success = True
    elif 'error_message' not in locals() or not error_message:
        error_details = stderr_output.strip()[:500] if stderr_output.strip() else "No STDERR output."
        error_message = f"Blender exited with code {final_return_code}. Details: {error_details}"
        success = False

    logger.debug(f"--- STDOUT ---\n{stdout_output[-1000:]}")
    logger.debug(f"--- STDERR ---\n{stderr_output}")

    # This variable is defined but might not be used if render_settings is empty
    if temp_script_path and os.path.exists(temp_script_path):
        os.remove(temp_script_path)

    return success, was_canceled, stdout_output, stderr_output, error_message