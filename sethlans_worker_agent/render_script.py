# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
# Created by Mario Estrella on 07/22/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
# sethlans_worker_agent/render_script.py
"""
Generates Python scripts for configuring Blender render settings.

This module creates the configuration scripts that Blender executes
before rendering to apply engine, device, and user-defined settings.
"""
import logging
import re
from typing import Optional

from sethlans_worker_agent import config, system_monitor

logger = logging.getLogger(__name__)

# Allowlist of valid Blender render engines. This must match the values
# accepted by bpy.context.scene.render.engine. Sourced from the
# RenderEngine enum in workers/constants.py, plus the legacy EEVEE name
# for backward compatibility with Blender < 4.0.
_VALID_ENGINES = {
    'CYCLES', 'BLENDER_EEVEE', 'BLENDER_EEVEE_NEXT', 'WORKBENCH',
}

# Pattern for safe render setting keys: must be a valid Python
# attribute path (e.g. "render.resolution_x", "cycles.samples").
_SAFE_KEY_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_.]*$')


def _resolve_target_gpu_index(gpu_index_override):
    """
    Determines the target GPU index from override or config.

    Returns the resolved integer index, or None if no specific
    GPU should be targeted (use all available GPUs).
    """
    if gpu_index_override is not None:
        logger.info(
            "GPU split mode: Targeting specific GPU index "
            f"{gpu_index_override}."
        )
        return gpu_index_override

    if config.FORCE_GPU_INDEX is not None:
        try:
            idx = int(config.FORCE_GPU_INDEX)
            logger.warning(f"FORCE_GPU_INDEX is set globally to {idx}.")
            return idx
        except (ValueError, TypeError):
            logger.error(
                "Invalid SETHLANS_FORCE_GPU_INDEX: "
                f"'{config.FORCE_GPU_INDEX}'. Using all GPUs."
            )
    return None


def _append_gpu_isolation_script(script_lines, target_index):
    """Appends Blender script lines to isolate a specific GPU by index."""
    script_lines.append(f"target_gpu_index = {target_index}")
    script_lines.append(
        "non_cpu_devices = "
        "[d for d in prefs.devices if d.type != 'CPU']"
    )
    # First, disable all devices to ensure a clean slate.
    script_lines.append(
        "for device in prefs.devices: device.use = False"
    )
    script_lines.append(
        "if 0 <= target_gpu_index < len(non_cpu_devices):"
    )
    script_lines.append(
        "    target_device = non_cpu_devices[target_gpu_index]"
    )
    script_lines.append(
        "    print(f'Isolating GPU: {target_device.name}')"
    )
    # Then, enable only the target GPU.
    script_lines.append("    target_device.use = True")
    script_lines.append("else:")
    script_lines.append(
        "    print(f'WARNING: GPU index {target_gpu_index} "
        "is out of valid range [0, {len(non_cpu_devices)-1}]."
        " Using all GPUs.')"
    )
    script_lines.append(
        "    for device in non_cpu_devices: device.use = True"
    )


def _append_cycles_gpu_config(script_lines, job_id,
                              detected_gpus, gpu_index_override):
    """Appends Blender script lines for Cycles GPU rendering config."""
    logger.info(
        f"[Job {job_id}] Configuring job for GPU rendering. "
        f"Available backends: {detected_gpus}"
    )
    script_lines.append(
        "prefs = bpy.context.preferences"
        ".addons['cycles'].preferences"
    )
    backend_preference = ['OPTIX', 'CUDA', 'HIP', 'METAL', 'ONEAPI']
    chosen_backend = next(
        (b for b in backend_preference if b in detected_gpus), None
    )

    if chosen_backend:
        script_lines.append(
            f"prefs.compute_device_type = '{chosen_backend}'"
        )
        script_lines.append(
            f"print(f'Using compute backend: {chosen_backend}')"
        )
        script_lines.append("prefs.get_devices()")

        target_index = _resolve_target_gpu_index(gpu_index_override)
        if target_index is not None:
            _append_gpu_isolation_script(script_lines, target_index)
        else:
            # Default behavior: enable all detected GPUs
            script_lines.append("for device in prefs.devices:")
            script_lines.append(
                "    if device.type != 'CPU': device.use = True"
            )

        script_lines.append(
            "bpy.context.scene.cycles.device = 'GPU'"
        )
    else:
        logger.warning(
            "GPU requested but no compatible backend was "
            "detected. Falling back to CPU."
        )
        script_lines.append(
            "bpy.context.scene.cycles.device = 'CPU'"
        )


def generate_render_config_script(
    job_id, render_engine, render_device, render_settings,
    gpu_index_override: Optional[int] = None,
    is_cpu_fallback: bool = False
):
    """
    Generates a Python script to configure Blender's render settings.

    This script is executed by Blender to ensure settings are applied
    before rendering. It supports overriding the render engine, device,
    and other user-defined settings.

    For GPU jobs, it can be configured to isolate a single, specific GPU
    device by its index via `gpu_index_override` (split mode) or the
    `SETHLANS_FORCE_GPU_INDEX` configuration setting.

    Args:
        job_id (int): The ID of the job for logging purposes.
        render_engine (str): The render engine (e.g., 'CYCLES').
        render_device (str): The render device ('CPU', 'GPU', 'ANY').
        render_settings (dict): User-defined settings to override.
        gpu_index_override (int, optional): A specific GPU device index.
            Takes precedence over FORCE_GPU_INDEX. Defaults to None.
        is_cpu_fallback (bool): If True, forces CPU device config,
            overriding an 'ANY' preference. Defaults to False.

    Returns:
        str: The complete Python script as a string.

    Raises:
        ValueError: If render_engine is not in the allowlist.
    """
    if render_engine not in _VALID_ENGINES:
        logger.error(
            f"[Job {job_id}] Invalid render engine: {render_engine!r}. "
            f"Allowed engines: {sorted(_VALID_ENGINES)}"
        )
        raise ValueError(
            f"Invalid render engine: {render_engine!r}. "
            f"Must be one of {sorted(_VALID_ENGINES)}"
        )

    script_lines = ["import bpy"]

    # Set the render engine FIRST to ensure the context is correct.
    script_lines.append(
        f"bpy.context.scene.render.engine = '{render_engine}'"
    )

    # Only configure Cycles-specific device settings for Cycles.
    if render_engine == 'CYCLES':
        detected_gpus = system_monitor.detect_gpu_devices()
        use_gpu = (
            not is_cpu_fallback
            and (render_device == 'GPU'
                 or (render_device == 'ANY' and detected_gpus))
        )

        if use_gpu:
            _append_cycles_gpu_config(
                script_lines, job_id, detected_gpus,
                gpu_index_override
            )
        else:
            logger.info(
                f"[Job {job_id}] Configuring job for CPU rendering."
            )
            script_lines.append(
                "bpy.context.scene.cycles.device = 'CPU'"
            )

    # --- User Overrides ---
    if isinstance(render_settings, dict) and render_settings:
        script_lines.append("# Applying user-defined render settings")
        script_lines.append("for scene in bpy.data.scenes:")
        for key, value in render_settings.items():
            if not _SAFE_KEY_RE.match(key):
                logger.warning(
                    f"[Job {job_id}] Skipping invalid render "
                    f"setting key: {key!r}"
                )
                continue
            py_value = repr(value)
            script_lines.append(f"    scene.{key} = {py_value}")

    return "\n".join(script_lines)
