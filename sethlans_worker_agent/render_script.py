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
from typing import Optional

from sethlans_worker_agent import config, system_monitor

logger = logging.getLogger(__name__)


def generate_render_config_script(job_id, render_engine, render_device, render_settings,
                                  gpu_index_override: Optional[int] = None, is_cpu_fallback: bool = False):
    """
    Generates a Python script to configure Blender's render settings.

    This script is executed by Blender to ensure settings are applied before
    rendering. It supports overriding the render engine, device, and other
    user-defined settings. It also injects `print()` statements to provide
    diagnostic logging about the chosen backend and device, which are captured
    from Blender's standard output.

    For GPU jobs, it can be configured to isolate a single, specific GPU device
    by its index. This is controlled by the `gpu_index_override` (for split mode)
    or the `SETHLANS_FORCE_GPU_INDEX` configuration setting.

    Args:
        job_id (int): The ID of the job for logging purposes.
        render_engine (str): The requested render engine (e.g., 'CYCLES').
        render_device (str): The requested render device ('CPU', 'GPU', 'ANY').
        render_settings (dict): A dictionary of user-defined settings to override.
        gpu_index_override (int, optional): A specific GPU device index to use.
            This takes precedence over FORCE_GPU_INDEX. Defaults to None.
        is_cpu_fallback (bool): If True, forces a CPU device configuration,
            overriding an 'ANY' preference. Defaults to False.

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
        # Determine if GPU should be used. The `is_cpu_fallback` flag is critical here to override
        # the 'ANY' preference when the job processor has decided to use the CPU.
        use_gpu = not is_cpu_fallback and ((render_device == 'GPU') or (render_device == 'ANY' and detected_gpus))

        if use_gpu:
            logger.info(f"[Job {job_id}] Configuring job for GPU rendering. Available backends: {detected_gpus}")
            script_lines.append("prefs = bpy.context.preferences.addons['cycles'].preferences")
            backend_preference = ['OPTIX', 'CUDA', 'HIP', 'METAL', 'ONEAPI']
            chosen_backend = next((b for b in backend_preference if b in detected_gpus), None)

            if chosen_backend:
                script_lines.append(f"prefs.compute_device_type = '{chosen_backend}'")
                script_lines.append(f"print(f'Using compute backend: {chosen_backend}')")
                script_lines.append("prefs.get_devices()")

                # Determine which GPU index to target, with override priority
                target_index = None
                if gpu_index_override is not None:
                    target_index = gpu_index_override
                    logger.info(f"GPU split mode: Targeting specific GPU index {target_index}.")
                elif config.FORCE_GPU_INDEX is not None:
                    try:
                        target_index = int(config.FORCE_GPU_INDEX)
                        logger.warning(f"FORCE_GPU_INDEX is set globally to {target_index}.")
                    except (ValueError, TypeError):
                        logger.error(f"Invalid SETHLANS_FORCE_GPU_INDEX: '{config.FORCE_GPU_INDEX}'. Using all GPUs.")

                if target_index is not None:
                    script_lines.append(f"target_gpu_index = {target_index}")
                    script_lines.append("non_cpu_devices = [d for d in prefs.devices if d.type != 'CPU']")
                    # First, disable all devices to ensure a clean slate.
                    script_lines.append("for device in prefs.devices: device.use = False")
                    script_lines.append("if 0 <= target_gpu_index < len(non_cpu_devices):")
                    script_lines.append("    target_device = non_cpu_devices[target_gpu_index]")
                    script_lines.append("    print(f'Isolating GPU: {target_device.name}')")
                    # Then, enable only the target GPU.
                    script_lines.append("    target_device.use = True")
                    script_lines.append("else:")
                    script_lines.append(f"    print(f'WARNING: GPU index {{target_gpu_index}} is out of valid range [0, {{len(non_cpu_devices)-1}}]. Using all GPUs.')")
                    script_lines.append("    for device in non_cpu_devices: device.use = True")
                else:
                    # Default behavior: enable all detected GPUs
                    script_lines.append("for device in prefs.devices:")
                    script_lines.append("    if device.type != 'CPU': device.use = True")

                script_lines.append("bpy.context.scene.cycles.device = 'GPU'")
            else:
                logger.warning(f"GPU requested but no compatible backend was detected. Falling back to CPU.")
                script_lines.append("bpy.context.scene.cycles.device = 'CPU'")
        else:
            logger.info(f"[Job {job_id}] Configuring job for CPU rendering.")
            script_lines.append("bpy.context.scene.cycles.device = 'CPU'")

    # --- User Overrides ---
    if isinstance(render_settings, dict) and render_settings:
        script_lines.append("# Applying user-defined render settings")
        script_lines.append("for scene in bpy.data.scenes:")
        for key, value in render_settings.items():
            py_value = repr(value)
            script_lines.append(f"    scene.{key} = {py_value}")

    return "\n".join(script_lines)
