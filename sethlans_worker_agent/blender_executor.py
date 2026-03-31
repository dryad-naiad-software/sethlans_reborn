# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2025 Dryad and Naiad Software LLC
# Created by Mario Estrella on 8/5/205.
# Dryad and Naiad Software LLC
#
# Project: sethlans_reborn
#
"""
Handles the direct interaction with the Blender command-line executable.

This module is responsible for constructing the full command-line arguments,
executing the Blender subprocess, and monitoring its execution until completion.
Script generation has been extracted to the render_script module.
"""
import datetime
import logging
import os
import platform
import subprocess
import tempfile
import threading
import time
from typing import Optional

import psutil
import requests

from sethlans_worker_agent import config, asset_manager, system_monitor
from sethlans_worker_agent.render_script import generate_render_config_script
from sethlans_worker_agent.tool_manager import tool_manager_instance

logger = logging.getLogger(__name__)


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


def execute_blender_job(job_data, assigned_gpu_index: Optional[int] = None):
    """
    Executes a Blender render job as a subprocess, with optional assignment to a specific GPU.

    This function now includes detailed, timed logging for each stage of the job's
    lifecycle, from initial assignment to final result reporting. It also captures
    and logs stdout from the Blender process for enhanced diagnostics. It can
    also apply a CPU thread limit based on the worker's configuration.

    Args:
        job_data (dict): The full job dictionary received from the manager API.
        assigned_gpu_index (int, optional): The device index of the GPU this job should
            be exclusively assigned to. Defaults to None.

    Returns:
        tuple: A tuple containing success status, cancellation status, outputs,
               error message, and the final output file path.
    """
    job_id = job_data.get('id')
    job_name = job_data.get('name', 'Unnamed Job')
    render_device = job_data.get('render_device', 'CPU')

    logger.info(f"[Job {job_id}] Received job '{job_name}'. Job execution started at {datetime.datetime.now(datetime.timezone.utc).isoformat()}.")

    final_gpu_index_to_log = assigned_gpu_index
    if final_gpu_index_to_log is None and config.FORCE_GPU_INDEX is not None:
        try:
            final_gpu_index_to_log = int(config.FORCE_GPU_INDEX)
        except (ValueError, TypeError):
            pass

    if final_gpu_index_to_log is not None:
        all_physical_gpus = system_monitor.get_gpu_device_details()
        if 0 <= final_gpu_index_to_log < len(all_physical_gpus):
            gpu_details = all_physical_gpus[final_gpu_index_to_log]
            gpu_name = gpu_details.get('name', 'N/A')
            logger.info(f"[Job {job_id}] Assigning to [Physical GPU {final_gpu_index_to_log}] {gpu_name}.")
        else:
            logger.warning(
                f"[Job {job_id}] Requested GPU index {final_gpu_index_to_log} is out of valid range. "
                f"Blender will use all available GPUs."
            )

    output_file_pattern = job_data.get('output_file_pattern')
    start_frame = job_data.get('start_frame', 1)
    end_frame = job_data.get('end_frame', 1)
    blender_version_req = job_data.get('blender_version')
    render_engine = job_data.get('render_engine', 'CYCLES')
    render_settings = job_data.get('render_settings', {})
    temp_script_path = None

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

    # --- REFACTORED: Determine CPU thread limit ---
    cpu_threads_to_use = 0
    # Priority 1: Manual override from config has the highest priority.
    if config.CPU_THREADS > 0:
        cpu_threads_to_use = config.CPU_THREADS
        logger.info(f"Applying manual CPU thread limit from config: {cpu_threads_to_use} threads.")
    # --- NEW: Priority 2: Automatic calculation for mixed-mode (CPU+GPU) workers ---
    elif not config.FORCE_CPU_ONLY and not config.FORCE_GPU_ONLY:
        gpus = system_monitor.get_gpu_device_details()
        num_gpus = len(gpus)
        if num_gpus > 0:
            total_threads = system_monitor.get_cpu_thread_count()
            # Reserve one thread per active GPU task to leave headroom.
            calculated_threads = total_threads - num_gpus
            # Ensure we always leave at least one thread for Blender.
            cpu_threads_to_use = max(1, calculated_threads)
            logger.info(
                f"Applying automatic CPU thread limit for mixed-mode operation. "
                f"Total: {total_threads}, GPUs: {num_gpus}, Blender Threads: {cpu_threads_to_use}"
            )

    # Add the --threads flag if the job is CPU-bound and a limit is set.
    is_cpu_bound = render_device != 'GPU'
    if is_cpu_bound and cpu_threads_to_use > 0:
        command.extend(["--threads", str(cpu_threads_to_use)])
    # --- END REFACTORED SECTION ---

    try:
        # Determine if this is a CPU fallback scenario
        gpus_on_system = system_monitor.get_gpu_device_details()
        is_cpu_fallback_case = (
                render_device == 'ANY' and
                assigned_gpu_index is None and
                len(gpus_on_system) > 0
        )
        if is_cpu_fallback_case:
            logger.info(f"[Job {job_id}] [CPU Fallback] Forcing CPU configuration for 'ANY' job as all GPUs are busy.")

        script_content = generate_render_config_script(
            job_id, render_engine, render_device, render_settings,
            gpu_index_override=assigned_gpu_index,
            is_cpu_fallback=is_cpu_fallback_case
        )
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

        logger.info(f"[Job {job_id}] Blender subprocess starting at {datetime.datetime.now(datetime.timezone.utc).isoformat()}...")
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

        stdout_thread.join()
        stderr_thread.join()
        logger.info(f"[Job {job_id}] Blender subprocess finished at {datetime.datetime.now(datetime.timezone.utc).isoformat()}.")

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

    if stdout_lines:
        logger.debug(f"--- [Job {job_id}] Blender STDOUT ---")
        for line in stdout_lines:
            if line.strip():
                logger.debug(f"[Job {job_id}] {line.strip()}")
    if stderr_lines:
        logger.warning(f"--- [Job {job_id}] Blender STDERR ---")
        for line in stderr_lines:
            if line.strip():
                logger.warning(f"[Job {job_id}] {line.strip()}")

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

    logger.info(f"[Job {job_id}] Job execution finished at {datetime.datetime.now(datetime.timezone.utc).isoformat()}.")
    if success:
        logger.info(f"[Job {job_id}] Result: SUCCESS. Output file: {final_output_path}")
    elif was_canceled:
        logger.info(f"[Job {job_id}] Result: CANCELED.")
    else:
        logger.error(f"[Job {job_id}] Result: FAILED. Error: {error_message}")

    return success, was_canceled, stdout_output, stderr_output, error_message, final_output_path
