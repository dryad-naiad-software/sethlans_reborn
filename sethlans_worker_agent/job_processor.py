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
import math
import os
import re
import requests
import subprocess
import time
import platform
import threading
import psutil
import tempfile

from sethlans_worker_agent import config, asset_manager, system_monitor
from sethlans_worker_agent.tool_manager import tool_manager_instance

logger = logging.getLogger(__name__)


def _upload_render_output(job_id, output_file_path):
    """Uploads the rendered output file to the manager."""
    if not os.path.exists(output_file_path):
        logger.error(f"Render output file not found at {output_file_path}. Cannot upload.")
        return False

    upload_url = f"{config.MANAGER_API_URL}jobs/{job_id}/upload_output/"
    logger.info(f"Uploading render output {output_file_path} to {upload_url}...")

    try:
        with open(output_file_path, 'rb') as f:
            files = {'output_file': (os.path.basename(output_file_path), f, 'image/png')}
            response = requests.post(upload_url, files=files, timeout=60)
            response.raise_for_status()
        logger.info(f"Successfully uploaded output file for job {job_id}.")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to upload output file for job {job_id}: {e}")
        return False
    except FileNotFoundError:
        logger.error(f"File not found during upload attempt for job {job_id}: {output_file_path}")
        return False


def _parse_render_time(stdout_text):
    """
    Parses Blender's stdout log content to find the total render time by
    finding the unique final summary line containing "(Saving:)". This is the most
    robust method, as it avoids issues with progress bar output.
    """
    time_line_regex = re.compile(r"Time: (?:(\d{2}):)?(\d{2}):(\d{2}\.\d{2})")

    for line in stdout_text.splitlines():
        if "(Saving:" in line:
            match = time_line_regex.search(line)
            if match:
                try:
                    hours_str = match.group(1)
                    minutes_str = match.group(2)
                    seconds_str = match.group(3)

                    hours = int(hours_str) if hours_str else 0
                    minutes = int(minutes_str)
                    seconds = float(seconds_str)

                    total_seconds = int(math.ceil((hours * 3600) + (minutes * 60) + seconds))
                    logger.info(f"Parsed render time: {total_seconds} seconds from line: '{line.strip()}'")
                    return total_seconds
                except (IndexError, ValueError) as e:
                    logger.warning(f"Found summary line but failed to parse time: '{line.strip()}' - {e}")
                    return None

    logger.warning("Could not find the final 'Time: ... (Saving: ...)' summary line in the render output.")
    return None


def update_job_status(job_url, payload):
    """Updates the job status via the API."""
    try:
        response = requests.patch(job_url, json=payload, timeout=5)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to update job status at {job_url}: {e}")


def get_and_claim_job(worker_id):
    """
    Polls for jobs based on worker capabilities, claims the first available one, and processes it.
    """
    poll_url = f"{config.MANAGER_API_URL}jobs/"

    # Check for GPU capability and add to query params.
    # This uses the cached result from the initial system_monitor run.
    detected_gpus = system_monitor.detect_gpu_devices()
    gpu_available = len(detected_gpus) > 0

    params = {
        'status': 'QUEUED',
        'assigned_worker__isnull': 'true',
        'gpu_available': str(gpu_available).lower()
    }
    logger.debug(f"Polling for jobs with params: {params}")

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

                success, was_canceled, stdout, stderr, blender_error_msg, final_output_path = execute_blender_job(
                    job_to_claim)

                job_update_payload = {
                    "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z'),
                    "last_output": stdout,
                    "error_message": blender_error_msg,
                }

                render_time = _parse_render_time(stdout)
                if render_time is not None:
                    job_update_payload["render_time_seconds"] = render_time

                if success:
                    job_update_payload["status"] = "DONE"
                    if final_output_path:
                        upload_ok = _upload_render_output(job_id, final_output_path)
                        if upload_ok:
                            try:
                                logger.info(f"Cleaning up local render output: {final_output_path}")
                                os.remove(final_output_path)
                                output_dir = os.path.dirname(final_output_path)
                                if not os.listdir(output_dir):
                                    os.rmdir(output_dir)
                            except OSError as e:
                                logger.warning(f"Could not clean up temporary render file or directory: {e}")

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
    Returns (success: bool, was_canceled: bool, stdout: str, stderr: str, error_message: str, output_path: str|None)
    """
    job_id = job_data.get('id')
    job_name = job_data.get('name', 'Unnamed Job')
    output_file_pattern = job_data.get('output_file_pattern')
    start_frame = job_data.get('start_frame', 1)
    end_frame = job_data.get('end_frame', 1)
    blender_version_req = job_data.get('blender_version')
    render_engine = job_data.get('render_engine', 'CYCLES')
    render_settings = job_data.get('render_settings', {})
    render_device = job_data.get('render_device', 'CPU')
    temp_script_path = None

    logger.info(f"Starting render for job '{job_name}' (ID: {job_id})...")

    os.makedirs(config.WORKER_TEMP_DIR, exist_ok=True)

    local_blend_file_path = asset_manager.ensure_asset_is_available(job_data.get('asset'))
    if not local_blend_file_path:
        error_message = "Failed to download or find the required .blend file asset."
        logger.error(error_message)
        return False, False, "", "", error_message, None

    blender_to_use = tool_manager_instance.ensure_blender_version_available(
        blender_version_req) if blender_version_req else config.SYSTEM_BLENDER_EXECUTABLE
    if not blender_to_use:
        error_message = f"Could not find or acquire Blender version '{blender_version_req}'. Aborting job."
        return False, False, "", "", error_message, None

    logger.info(f"Using Blender executable: {blender_to_use}")

    resolved_output_pattern = os.path.normpath(os.path.join(config.WORKER_OUTPUT_DIR, output_file_pattern))

    output_dir = os.path.dirname(resolved_output_pattern)
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    command = [
        blender_to_use, "-b", local_blend_file_path
    ]

    if isinstance(render_settings, dict) and render_settings:
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, dir=config.WORKER_TEMP_DIR) as f:
                temp_script_path = f.name
                script_lines = ["import bpy", "for scene in bpy.data.scenes:"]
                for key, value in render_settings.items():
                    py_value = repr(value)
                    script_lines.append(f"    scene.{key} = {py_value}")

                script_content = "\n".join(script_lines)
                f.write(script_content)
                logger.debug(f"Generated override script at {temp_script_path}:\n{script_content}")

            command.extend(["--python", temp_script_path])
        except Exception as e:
            logger.error(f"Failed to generate render settings script: {e}")
            if temp_script_path and os.path.exists(temp_script_path):
                os.remove(temp_script_path)
            return False, False, "", "", f"Failed to generate settings script: {e}", None

    command.extend([
        "-o", resolved_output_pattern,
        "-F", "PNG", "-E", render_engine,
    ])

    if render_device == 'CPU':
        command.insert(1, "--factory-startup")
        logger.info("CPU job detected. Using --factory-startup to ensure CPU rendering.")
    else:
        logger.info("GPU job detected. Omitting --factory-startup to use pre-configured .blend file settings.")

    if start_frame == end_frame:
        command.extend(["-f", str(start_frame)])
    else:
        command.extend(["-s", str(start_frame), "-e", str(end_frame), "-a"])

    logger.info(f"Running Command: {' '.join(command)}")

    process = None
    was_canceled = False
    stdout_lines, stderr_lines, error_message = [], [], ""
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

        while process.poll() is None:
            try:
                response = requests.get(job_url, timeout=5)
                if response.status_code == 200 and response.json().get('status') == 'CANCELED':
                    logger.warning(f"Cancellation signal for job ID {job_id} received. Terminating process tree.")
                    parent = psutil.Process(process.pid)
                    for child in parent.children(recursive=True):
                        child.kill()
                    parent.kill()
                    was_canceled = True
                    break
            except (requests.exceptions.RequestException, psutil.NoSuchProcess):
                if not psutil.pid_exists(process.pid):
                    break

            time.sleep(2)

        stdout_thread.join()
        stderr_thread.join()

        final_return_code = process.wait()

    except Exception as e:
        error_message = f"An unexpected error occurred during Blender execution: {e}"
        final_return_code = -1
    finally:
        if temp_script_path and os.path.exists(temp_script_path):
            os.remove(temp_script_path)

    stdout_output = "".join(stdout_lines)
    stderr_output = "".join(stderr_lines)
    success = False
    final_output_path = None

    if was_canceled:
        error_message = "Job was canceled by user request."
    elif final_return_code == 0:
        logger.info("Render command completed successfully.")
        error_message = ""
        success = True
        if start_frame == end_frame:
            final_output_path = resolved_output_pattern.replace("####", f"{start_frame:04d}") + ".png"
    elif not error_message:
        error_details = stderr_output.strip()[:500] if stderr_output.strip() else "No STDERR output."
        error_message = f"Blender exited with code {final_return_code}. Details: {error_details}"

    logger.debug(f"--- STDOUT ---\n{stdout_output[-1000:]}")
    logger.debug(f"--- STDERR ---\n{stderr_output}")

    return success, was_canceled, stdout_output, stderr_output, error_message, final_output_path