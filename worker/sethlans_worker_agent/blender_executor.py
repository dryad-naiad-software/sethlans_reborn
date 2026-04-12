# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Blender subprocess execution: command construction, monitoring, and output.

Script generation is in render_script.py; thumbnail passes in blender_thumbnail.py.
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

from sethlans_worker_agent import config, asset_manager, system_monitor, api_handler
from sethlans_worker_agent.blender_yield import (
    terminate_process_tree, handle_yield,
)
from sethlans_worker_agent.format_utils import get_format_and_extension, EXR_HDR_FORMATS
from sethlans_worker_agent.render_script import generate_render_config_script
from sethlans_worker_agent.tool_manager import tool_manager_instance

logger = logging.getLogger(__name__)

# Thread-safe dict of last Blender stdout line per job ID.
# Lock ordering: _output_lock is below the WorkerCapacity lock, the
# _active_jobs_lock, and the _recent_jobs_lock in job_processor.
_last_output_lines = {}
_output_lock = threading.Lock()


def get_last_output_line(job_id):
    """Return the last Blender stdout line for a given job, or None."""
    with _output_lock:
        return _last_output_lines.get(job_id)


def _stream_reader(stream, output_list, job_id=None):
    """Read a subprocess stream line by line into a list."""
    try:
        for line in iter(stream.readline, ''):
            output_list.append(line)
            if job_id is not None:
                with _output_lock:
                    _last_output_lines[job_id] = line.rstrip()
    finally:
        stream.close()


def execute_blender_job(job_data, assigned_gpu_index: Optional[int] = None,  # noqa: C901
                        yield_monitor_factory=None,
                        manual_stop_event=None,
                        shutdown_event=None):
    """Execute a Blender render job as a subprocess.

    Args:
        yield_monitor_factory: Optional callable(blender_pid) that creates
            and starts a YieldMonitor. Called after the subprocess launches.

    Returns (success, was_canceled, was_yielded, stdout, stderr,
             error_msg, output_path, thumbnail_path, yield_monitor,
             grace_outcome).
    """
    job_id = job_data.get('id')
    job_name = job_data.get('name', 'Unnamed Job')
    render_device = job_data.get('render_device', 'CPU')

    now_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
    logger.info(f"[Job {job_id}] Received '{job_name}'. Started at {now_utc}.")

    gpu_idx = assigned_gpu_index
    if gpu_idx is None and config.FORCE_GPU_INDEX is not None:
        try:
            gpu_idx = int(config.FORCE_GPU_INDEX)
        except (ValueError, TypeError):
            pass
    if gpu_idx is not None:
        gpus = system_monitor.get_gpu_device_details()
        if 0 <= gpu_idx < len(gpus):
            logger.info(f"[Job {job_id}] Assigning to [Physical GPU {gpu_idx}] {gpus[gpu_idx].get('name', 'N/A')}.")
        else:
            logger.warning(f"[Job {job_id}] GPU index {gpu_idx} out of range. Using all available GPUs.")

    output_file_pattern = job_data.get('output_file_pattern')
    start_frame = job_data.get('start_frame', 1)
    end_frame = job_data.get('end_frame', 1)
    # Use effective_blender_version (which resolves project defaults)
    # when no explicit blender_version override is set on the job.
    effective = job_data.get('effective_blender_version') or {}
    blender_version_req = effective.get(
        'resolved_version',
        job_data.get('blender_version'),
    )
    render_engine = job_data.get('render_engine', 'CYCLES')
    render_settings = job_data.get('render_settings', {})
    temp_script_path = None

    os.makedirs(config.WORKER_TEMP_DIR, exist_ok=True)

    local_blend_file_path = asset_manager.ensure_asset_is_available(job_data.get('asset'))
    if not local_blend_file_path:
        return False, False, "", "", "Failed to download or find the required .blend file asset.", None, None

    blender_to_use = tool_manager_instance.ensure_blender_version_available(blender_version_req)
    if not blender_to_use:
        msg = f"Could not find or acquire Blender version '{blender_version_req}'. Aborting job."
        return False, False, "", "", msg, None, None
    # Acquire a reference count on this version to prevent cleanup
    # from deleting it while the render is in progress.
    resolved_version = blender_version_req
    tool_manager_instance.acquire_version(resolved_version)

    logger.info(f"Using Blender executable: {blender_to_use}")
    # Strip Blender's // prefix to avoid UNC path interpretation on Windows
    clean_pattern = output_file_pattern.lstrip('/')
    resolved_output_pattern = os.path.normpath(os.path.join(config.WORKER_OUTPUT_DIR, clean_pattern))
    os.makedirs(os.path.dirname(resolved_output_pattern), exist_ok=True)

    command = [blender_to_use, "--factory-startup", "-b", local_blend_file_path]
    from sethlans_worker_agent.blender_thread_cap import maybe_add_threads_flag
    maybe_add_threads_flag(command, job_id, assigned_gpu_index)

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
        return False, False, "", "", error_msg, None, None

    blender_format, _ext = get_format_and_extension(render_settings)
    command.extend(["-o", resolved_output_pattern, "-F", blender_format])

    if start_frame == end_frame:
        command.extend(["-f", str(start_frame)])
    else:
        command.extend(["-s", str(start_frame), "-e", str(end_frame), "-a"])

    logger.info(f"Running Command: {' '.join(command)}")
    process = None
    was_canceled, was_yielded = False, False
    grace_outcome = "aborted"
    stdout_lines, stderr_lines, error_message = [], [], ""
    final_return_code = -1
    yield_monitor = None

    try:
        popen_kwargs = {
            "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
            "encoding": 'utf-8', "errors": 'surrogateescape',
            "cwd": config.WORKER_ROOT,
        }
        if platform.system() == "Windows":
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        logger.info(f"[Job {job_id}] Blender subprocess starting...")
        process = subprocess.Popen(command, **popen_kwargs)
        logger.info(f"Blender subprocess launched with PID: {process.pid}")

        # Start yield monitor now that we have the PID
        if yield_monitor_factory is not None:
            yield_monitor = yield_monitor_factory(process.pid)
        yield_event = (
            yield_monitor.yield_event if yield_monitor else None
        )

        stdout_thread = threading.Thread(
            target=_stream_reader, args=(process.stdout, stdout_lines, job_id), daemon=True)
        stderr_thread = threading.Thread(
            target=_stream_reader, args=(process.stderr, stderr_lines), daemon=True)
        stdout_thread.start()
        stderr_thread.start()

        while process.poll() is None:
            # Cancellation check
            try:
                if api_handler.get_job_status(job_id) == 'CANCELED':
                    logger.warning(f"[Job {job_id}] Cancellation received.")
                    terminate_process_tree(process.pid, job_id)
                    was_canceled = True
                    break
            except psutil.NoSuchProcess:
                if not psutil.pid_exists(process.pid):
                    break

            # Yield event check (FR-6)
            if yield_event is not None and yield_event.is_set():
                was_yielded, grace_outcome = handle_yield(
                    process, job_id, render_engine, yield_monitor,
                    manual_stop_event, shutdown_event,
                )
                break

            time.sleep(2)

        for t in (stdout_thread, stderr_thread):
            t.join(timeout=10)
        logger.info(f"[Job {job_id}] Blender subprocess finished.")

        final_return_code = process.wait()
        logger.info(f"Blender exit code: {final_return_code}")

    except Exception as e:
        error_message = f"Unexpected error during Blender execution: {e}"
        logger.critical(error_message, exc_info=True)
        final_return_code = -1
    finally:
        # Stop the yield monitor thread (must be in finally)
        if yield_monitor is not None:
            yield_monitor.stop()
        # Clean up live output tracking for this job
        with _output_lock:
            _last_output_lines.pop(job_id, None)
        if temp_script_path and os.path.exists(temp_script_path):
            os.remove(temp_script_path)

    stdout_output, stderr_output = "".join(stdout_lines), "".join(stderr_lines)
    success, final_output_path, thumbnail_path = False, None, None

    try:
        for lines, level, label in [(stdout_lines, logger.debug, "STDOUT"), (stderr_lines, logger.warning, "STDERR")]:
            if lines:
                level(f"--- [Job {job_id}] Blender {label} ---")
                for line in lines:
                    if line.strip():
                        level(f"[Job {job_id}] {line.strip()}")

        if was_canceled:
            error_message = "Job was canceled by user request."
        elif was_yielded and grace_outcome == "finished":
            # Frame completed during grace period -- treat as success
            logger.info("Render completed during yield grace period.")
            success = True
            if start_frame == end_frame:
                final_output_path = resolved_output_pattern.replace(
                    "####", f"{start_frame:04d}")
        elif was_yielded:
            error_message = "Job yielded to artist."
        elif final_return_code == 0:
            logger.info("Render command completed successfully.")
            success = True
            if start_frame == end_frame:
                final_output_path = resolved_output_pattern.replace(
                    "####", f"{start_frame:04d}")
        elif not error_message:
            error_details = stderr_output.strip()[:500] or "No STDERR."
            error_message = (
                f"Blender exited with code {final_return_code}. "
                f"Details: {error_details}"
            )

        if success and blender_format in EXR_HDR_FORMATS and start_frame == end_frame:
            from sethlans_worker_agent.blender_thumbnail import render_exr_hdr_thumbnail
            thumbnail_path = render_exr_hdr_thumbnail(
                job_data, blender_to_use, local_blend_file_path,
                start_frame, resolved_output_pattern, assigned_gpu_index)
    finally:
        tool_manager_instance.release_version(resolved_version)

    logger.info(f"[Job {job_id}] Result: "
                f"{'SUCCESS' if success else 'YIELDED' if was_yielded else 'CANCELED' if was_canceled else 'FAILED'}")

    return (success, was_canceled, was_yielded, stdout_output,
            stderr_output, error_message, final_output_path,
            thumbnail_path, yield_monitor, grace_outcome)
