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
"""
The core module for the worker agent responsible for handling all render job tasks.

This includes polling the manager for new jobs, claiming them, downloading assets,
executing the Blender subprocess, monitoring its progress, and reporting the
final status and output back to the manager.
"""

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


def _generate_render_config_script(render_engine, render_device, render_settings):
    """
    Generates the content for a Python script to configure Blender's render settings.
    This is executed via the `--python` command-line argument to ensure settings
    are applied before the render begins.

    Args:
        render_engine (str): The requested render engine (e.g., 'CYCLES').
        render_device (str): The requested render device ('CPU', 'GPU', or 'ANY').
        render_settings (dict): A dictionary of user-defined settings to override.

    Returns:
        str: The complete Python script as a string.
    """
    script_lines = ["import bpy"]

    # --- Engine and Device Configuration ---
    # 1. Set the render engine FIRST to ensure the context is correct.
    script_lines.append(f"bpy.context.scene.render.engine = '{render_engine}'")

    # 2. Only configure Cycles-specific device settings if the engine is Cycles.
    if render_engine == 'CYCLES':
        detected_gpus = system_monitor.detect_gpu_devices()
        use_gpu = (render_device == 'GPU') or (render_device == 'ANY' and detected_gpus)

        if use_gpu:
            logger.info(f"Configuring job for GPU rendering. Available backends: {detected_gpus}")
            script_lines.append("prefs = bpy.context.preferences.addons['cycles'].preferences")
            backend_preference = ['OPTIX', 'CUDA', 'HIP', 'METAL', 'ONEAPI']
            chosen_backend = next((b for b in backend_preference if b in detected_gpus), None)

            if chosen_backend:
                script_lines.append(f"prefs.compute_device_type = '{chosen_backend}'")
                script_lines.append("prefs.get_devices()")
                script_lines.append("for device in prefs.devices:")
                script_lines.append("    if device.type != 'CPU':")
                script_lines.append("        device.use = True")
                script_lines.append("bpy.context.scene.cycles.device = 'GPU'")
            else:
                logger.warning("GPU requested but no compatible backend was detected. Falling back to CPU.")
                script_lines.append("bpy.context.scene.cycles.device = 'CPU'")
        else:
            logger.info("Configuring job for CPU rendering.")
            script_lines.append("bpy.context.scene.cycles.device = 'CPU'")

    # --- User Overrides ---
    if isinstance(render_settings, dict) and render_settings:
        script_lines.append("# Applying user-defined render settings")
        script_lines.append("for scene in bpy.data.scenes:")
        for key, value in render_settings.items():
            py_value = repr(value)
            script_lines.append(f"    scene.{key} = {py_value}")

    return "\n".join(script_lines)


def _upload_render_output(job_id, output_file_path):
    """
    Uploads the rendered output file to the manager's dedicated API endpoint.

    Args:
        job_id (int): The ID of the job the file belongs to.
        output_file_path (str): The local filesystem path of the file to upload.

    Returns:
        bool: True if the upload was successful, False otherwise.
    """
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
    finding the unique final summary line containing "(Saving:)".

    Args:
        stdout_text (str): The full stdout log from the Blender subprocess.

    Returns:
        int or None: The total render time in seconds, rounded up to the nearest
                     whole number, or None if the time could not be parsed.
    """
    time_line_regex = re.compile(r"Time: (?:(\d{2}):)?(\d{2}):(\d{2}\.\d{2})")

    for line in stdout_text.splitlines():
        if "(Saving:" in line:
            match = time_line_regex.search(line)
            if match:
                try:
                    hours_str, minutes_str, seconds_str = match.groups()
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
    """
    Sends a PATCH request to the manager to update a job's status.

    Args:
        job_url (str): The full URL of the job to update.
        payload (dict): The data to send in the request body.
    """
    try:
        response = requests.patch(job_url, json=payload, timeout=5)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to update job status at {job_url}: {e}")


def get_and_claim_job(worker_id):
    """
    Polls the manager for available jobs, claims the first one found, and starts the render process.

    This function is the heart of the worker's operational loop. It sends a request to the
    manager's job list endpoint, applying filters based on the worker's configured hardware
    capabilities. If a job is available, it attempts to claim it by updating the `assigned_worker`
    field. Upon a successful claim, it initiates the rendering process.

    Args:
        worker_id (int): The unique ID of the worker, as assigned by the manager.
    """
    poll_url = f"{config.MANAGER_API_URL}jobs/"
    detected_gpus = system_monitor.detect_gpu_devices()
    gpu_available = len(detected_gpus) > 0

    if config.FORCE_GPU_ONLY and not gpu_available:
        logger.info("FORCE_GPU_ONLY is enabled, but no GPUs were detected. Skipping job poll.")
        return

    params = {'status': 'QUEUED', 'assigned_worker__isnull': 'true'}

    # Correctly set polling parameters based on forced hardware modes.
    if config.FORCE_GPU_ONLY:
        params['gpu_available'] = 'true'
    elif config.FORCE_CPU_ONLY:
        params['gpu_available'] = 'false'
    # If neither flag is set (normal operation), do NOT add the 'gpu_available'
    # parameter. This allows a GPU-capable worker to pick up both CPU and GPU jobs.

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

            logger.info(
                f"Found {len(available_jobs)} available job(s). Attempting to claim job '{job_name}' (ID: {job_id})...")
            claim_response = requests.patch(claim_url, json={"assigned_worker": worker_id}, timeout=5)

            if claim_response.status_code == 200:
                logger.info(f"Successfully claimed job '{job_name}'! Starting render...")
                update_job_status(claim_url, {"status": "RENDERING"})
                success, was_canceled, stdout, stderr, blender_error_msg, final_output_path = execute_blender_job(
                    job_to_claim)

                job_update_payload = {
                    "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z'),
                    "last_output": stdout, "error_message": blender_error_msg}
                render_time = _parse_render_time(stdout)
                if render_time is not None:
                    job_update_payload["render_time_seconds"] = render_time

                if success:
                    job_update_payload["status"] = "DONE"
                    if final_output_path and _upload_render_output(job_id, final_output_path):
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
    """
    Helper function to read a subprocess stream line by line into a list.
    This runs in a separate thread to prevent I/O deadlocks.
    """
    try:
        for line in iter(stream.readline, ''):
            output_list.append(line)
    finally:
        stream.close()


def execute_blender_job(job_data):
    """
    Executes a Blender render job as a subprocess, monitoring for cancellation
    and reading its output in a thread-safe manner.

    This function first prepares the environment by downloading the required asset
    and Blender version. It then constructs a command with a dynamically generated
    Python script to ensure consistent render settings. The subprocess is
    monitored for completion or a cancellation signal from the manager.

    Args:
        job_data (dict): The full job dictionary received from the manager API.

    Returns:
        tuple: A tuple containing:
            - bool: True if the render was successful, False otherwise.
            - bool: True if the job was canceled, False otherwise.
            - str: The captured stdout from the Blender process.
            - str: The captured stderr from the Blender process.
            - str: A human-readable error message, if applicable.
            - str or None: The final output file path if successful, otherwise None.
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
        return False, False, "", "", "Failed to download or find the required .blend file asset.", None

    blender_to_use = tool_manager_instance.ensure_blender_version_available(blender_version_req)
    if not blender_to_use:
        return False, False, "", "", f"Could not find or acquire Blender version '{blender_version_req}'. Aborting job.", None

    logger.info(f"Using Blender executable: {blender_to_use}")
    resolved_output_pattern = os.path.normpath(os.path.join(config.WORKER_OUTPUT_DIR, output_file_pattern))
    os.makedirs(os.path.dirname(resolved_output_pattern), exist_ok=True)

    command = [blender_to_use, "--factory-startup", "-b", local_blend_file_path]

    try:
        script_content = _generate_render_config_script(render_engine, render_device, render_settings)
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, dir=config.WORKER_TEMP_DIR) as f:
            temp_script_path = f.name
            f.write(script_content)
            logger.debug(f"Generated override script at {temp_script_path}:\n{script_content}")
        command.extend(["--python", temp_script_path])
    except Exception as e:
        error_msg = f"Failed to generate render settings script: {e}"
        logger.error(error_msg)
        if temp_script_path and os.path.exists(temp_script_path):
            os.remove(temp_script_path)
        return False, False, "", "", error_msg, None

    command.extend(["-o", resolved_output_pattern, "-F", "PNG"])

    if start_frame == end_frame:
        command.extend(["-f", str(start_frame)])
    else:
        command.extend(["-s", str(start_frame), "-e", str(end_frame), "-a"])

    logger.info(f"Running Command: {' '.join(command)}")
    process = None
    was_canceled, stdout_lines, stderr_lines, error_message = False, [], [], ""
    final_return_code = -1

    try:
        popen_kwargs = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "encoding": 'utf-8',
                        "errors": 'surrogateescape', "cwd": config.PROJECT_ROOT_FOR_WORKER}
        if platform.system() == "Windows":
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        logger.debug("Attempting to launch Blender subprocess...")
        process = subprocess.Popen(command, **popen_kwargs)
        logger.info(f"Blender subprocess launched with PID: {process.pid}")

        stdout_thread = threading.Thread(target=_stream_reader, args=(process.stdout, stdout_lines))
        stderr_thread = threading.Thread(target=_stream_reader, args=(process.stderr, stderr_lines))
        stdout_thread.start()
        stderr_thread.start()
        job_url = f"{config.MANAGER_API_URL}jobs/{job_id}/"

        while process.poll() is None:
            logger.debug(f"Polling subprocess... still running. Checking for cancellation signal.")
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

        logger.info("Blender subprocess polling loop finished.")
        stdout_thread.join()
        stderr_thread.join()

        final_return_code = process.wait()
        logger.info(f"Blender subprocess finished with exit code: {final_return_code}")

    except Exception as e:
        error_message = f"An unexpected error occurred during Blender execution: {e}"
        logger.critical(error_message, exc_info=True)
        final_return_code = -1
    finally:
        if temp_script_path and os.path.exists(temp_script_path):
            os.remove(temp_script_path)

    stdout_output, stderr_output = "".join(stdout_lines), "".join(stderr_lines)
    success, final_output_path = False, None

    if was_canceled:
        error_message = "Job was canceled by user request."
    elif final_return_code == 0:
        logger.info("Render command completed successfully.")
        success = True
        if start_frame == end_frame:
            final_output_path = resolved_output_pattern.replace("####", f"{start_frame:04d}") + ".png"
    elif not error_message:
        error_details = stderr_output.strip()[:500] if stderr_output.strip() else "No STDERR output."
        error_message = f"Blender exited with code {final_return_code}. Details: {error_details}"

    logger.debug(f"--- STDOUT ---\n{stdout_output[-1000:]}")
    logger.debug(f"--- STDERR ---\n{stderr_output}")

    return success, was_canceled, stdout_output, stderr_output, error_message, final_output_path