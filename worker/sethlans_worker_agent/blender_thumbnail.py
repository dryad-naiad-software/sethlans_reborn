# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Worker-side EXR/HDR thumbnail generation via a second Blender render pass.

Pillow cannot read EXR or HDR files, so the manager cannot generate
thumbnails server-side for these formats. This module runs a low-resolution
PNG render pass after a successful primary EXR/HDR render and returns the
path to the resulting thumbnail file.

The thumbnail pass is best-effort: if it fails, the primary job still
succeeds with no thumbnail.
"""
import logging
import os
import subprocess
import tempfile
import threading

from sethlans_worker_agent import config
from sethlans_worker_agent import env_cleanup
from sethlans_worker_agent.blender_stop import popen_group_kwargs, wait_or_stop
from sethlans_worker_agent.blender_failure import (
    describe_blender_failure, python_script_args, request_recheck_if_check_failed,
)
from sethlans_worker_agent.render_script import generate_render_config_script

logger = logging.getLogger(__name__)


def _round_even(n):
    """Round a number to the nearest even integer."""
    return int(round(n / 2.0) * 2)


def _build_thumbnail_settings(render_settings, render_engine):
    """
    Build a fresh render_settings dict for the thumbnail pass.

    Only reads resolution dimensions from the primary render_settings
    to compute scaled thumbnail dimensions. All other keys are set
    from scratch to avoid inheriting EXR color depth, JPEG quality,
    or custom resolution percentages.
    """
    orig_x = render_settings.get('render.resolution_x', 1920)
    orig_y = render_settings.get('render.resolution_y', 1080)

    # Clamp width: at least 256px, at most 480px
    thumb_x = _round_even(min(480, max(256, orig_x)))

    # Scale height proportionally, preserving aspect ratio
    if orig_x > 0:
        thumb_y = _round_even(thumb_x * orig_y / orig_x)
    else:
        thumb_y = _round_even(thumb_x * 9 / 16)

    # Ensure at least 2px height
    thumb_y = max(2, thumb_y)

    thumb_settings = {
        'render.resolution_x': thumb_x,
        'render.resolution_y': thumb_y,
        'render.resolution_percentage': 100,
        'render.image_settings.file_format': 'PNG',
        'render.image_settings.color_depth': '8',
    }

    # Reduce Cycles samples for fast preview
    if render_engine == 'CYCLES':
        thumb_settings['cycles.samples'] = 32

    return thumb_settings


def _build_thumbnail_output_pattern(resolved_output_pattern):
    """
    Build the thumbnail output file pattern from the primary pattern.

    Replaces the filename stem with a thumb_ prefix and changes the
    extension to .png. Example:
        input:  /output/my_render_####.exr
        output: /output/thumb_my_render_####.png
    """
    dir_part = os.path.dirname(resolved_output_pattern)
    base = os.path.basename(resolved_output_pattern)
    stem, _ = os.path.splitext(base)
    thumb_name = f"thumb_{stem}.png"
    return os.path.join(dir_part, thumb_name)


def render_exr_hdr_thumbnail(
    job_data, blender_path, blend_file_path,
    start_frame, resolved_output_pattern, assigned_gpu_index, stop_event=None
):
    """
    Execute a low-resolution PNG thumbnail render pass for EXR/HDR jobs.

    Builds a fresh render_settings dict from scratch, re-applies GPU
    device configuration, and runs Blender as a subprocess.

    Args:
        job_data: The full job data dict.
        blender_path: Path to the Blender executable.
        blend_file_path: Path to the .blend file.
        start_frame: The frame number to render.
        resolved_output_pattern: The primary render output pattern.
        assigned_gpu_index: GPU index for device isolation, or None.
        stop_event: The worker's shutdown event; when it is set during the
            pass, the thumbnail's process tree is stopped (#247).

    Returns:
        Path to the rendered thumbnail PNG file, or None on failure.
    """
    job_id = job_data.get('id')
    render_engine = job_data.get('render_engine', 'CYCLES')
    render_device = job_data.get('render_device', 'CPU')
    render_settings = job_data.get('render_settings', {})

    logger.info(
        f"[Job {job_id}] Starting EXR/HDR thumbnail render pass."
    )

    thumb_settings = _build_thumbnail_settings(
        render_settings, render_engine
    )
    thumb_output = _build_thumbnail_output_pattern(
        resolved_output_pattern
    )

    if stop_event is not None and stop_event.is_set():  # #247: no new Blender during shutdown
        logger.info(f"[Job {job_id}] Worker is shutting down: skipping the thumbnail pass.")
        return None
    temp_script_path = None
    try:
        # Generate config script with thumbnail settings, re-applying
        # GPU configuration since --factory-startup resets preferences.
        script_content = generate_render_config_script(
            job_id, render_engine, render_device, thumb_settings,
            gpu_index_override=assigned_gpu_index,
            is_cpu_fallback=False
        )

        os.makedirs(config.WORKER_TEMP_DIR, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode='w', encoding='utf-8', suffix='.py', delete=False,
            dir=config.WORKER_TEMP_DIR
        ) as f:
            temp_script_path = f.name
            f.write(script_content)

        command = [
            blender_path, "--factory-startup", "-b",
            blend_file_path, *python_script_args(temp_script_path),
            "-o", thumb_output, "-F", "PNG",
            "-f", str(start_frame),
        ]

        logger.info(
            f"[Job {job_id}] Thumbnail command: {' '.join(command)}"
        )

        popen_kwargs = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "encoding": 'utf-8',
            "errors": 'surrogateescape',
            "cwd": os.path.dirname(blend_file_path),
            "env": env_cleanup.clean_env_for_blender(blend_file_path),
        }
        popen_kwargs.update(popen_group_kwargs())  # own process group/session (#247)

        process = subprocess.Popen(command, **popen_kwargs)

        # Read output without manager polling (short render); a worker
        # shutdown still stops it (#247).
        stdout_lines, stderr_lines = [], []
        stdout_t = threading.Thread(
            target=_drain_stream,
            args=(process.stdout, stdout_lines),
            daemon=True,
        )
        stderr_t = threading.Thread(
            target=_drain_stream,
            args=(process.stderr, stderr_lines),
            daemon=True,
        )
        stdout_t.start()
        stderr_t.start()

        return_code = wait_or_stop(process, stop_event, job_id)
        stdout_t.join(timeout=10)
        stderr_t.join(timeout=10)
        if return_code is None:  # stopped: drop any partial thumbnail
            partial = thumb_output.replace("####", f"{start_frame:04d}")
            if os.path.isfile(partial):
                os.remove(partial)
            return None

        if return_code != 0:
            # Best-effort: a failed GPU device check (#238), a failed
            # settings script (#240) or any other error only drops the
            # thumbnail, never the primary job.
            stderr_text = "".join(stderr_lines)
            reason = describe_blender_failure(return_code, stderr_text)
            # A failed device check may mean a stale card list (#239).
            request_recheck_if_check_failed(return_code, stderr_text)
            logger.warning(
                f"[Job {job_id}] Thumbnail render failed: {reason}"
            )
            return None

        # Resolve the actual thumbnail file path
        thumb_path = thumb_output.replace(
            "####", f"{start_frame:04d}"
        )
        if os.path.exists(thumb_path):
            logger.info(
                f"[Job {job_id}] Thumbnail generated: {thumb_path}"
            )
            return thumb_path

        logger.warning(
            f"[Job {job_id}] Thumbnail file not found at "
            f"{thumb_path} after successful render."
        )
        return None

    except Exception as e:
        logger.warning(
            f"[Job {job_id}] Thumbnail render pass failed: {e}"
        )
        return None
    finally:
        if temp_script_path and os.path.exists(temp_script_path):
            os.remove(temp_script_path)


def _drain_stream(stream, output_list):
    """Read all lines from a stream into a list, then close it."""
    try:
        for line in iter(stream.readline, ''):
            output_list.append(line)
    finally:
        stream.close()
