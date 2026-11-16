# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Generates Python scripts for configuring Blender render settings.

This module creates the configuration scripts that Blender executes
before rendering to apply engine, device, and user-defined settings.
"""
import logging
import re
from typing import Optional

from sethlans_worker_agent import config, gpu_device_script, system_monitor
from sethlans_worker_agent.engine_script import ENGINE_HELPER, ENGINE_IDENTIFIERS, engine_call, is_known_engine

logger = logging.getLogger(__name__)

# Allowlist of engine names a job may carry: the product's RenderEngine
# values in workers/constants.py (CYCLES, BLENDER_EEVEE_NEXT, WORKBENCH)
# plus Blender's own BLENDER_EEVEE and BLENDER_WORKBENCH. The script sets
# whichever identifier of the engine's family the job's Blender accepts
# (engine_script.py, #242).
_VALID_ENGINES = frozenset(ENGINE_IDENTIFIERS)

# Pattern for safe render setting keys: must be a valid Python
# attribute path (e.g. "render.resolution_x", "cycles.samples").
_SAFE_KEY_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_.]*$')

# Removed from Cycles in Blender 5.x but still sent by the manager (#241);
# see _append_user_setting.
_FEATURE_SET_KEY = 'cycles.feature_set'


def _resolve_target_gpu_index(gpu_index_override):
    """
    Determines the target GPU index from override or config.

    The index counts physical cards: it is a position in
    ``system_monitor.get_gpu_device_details()``. Returns the resolved
    integer index, or None if no specific GPU should be targeted
    (combined mode: every listed card through the preferred backend).
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
                f"'{config.FORCE_GPU_INDEX}'. Ignoring it."
            )
    return None


def _append_cycles_gpu_config(script_lines, job_id,
                              detected_gpus, gpu_index_override):
    """Appends Blender script lines for Cycles GPU rendering config.

    With a target index (split mode or FORCE_GPU_INDEX) exactly one
    physical card is enabled, selected by its backend and Blender device
    id. Without one (combined mode) every entry of the machine-wide
    preferred backend that belongs to a card on the worker's list is
    enabled. Every other entry is switched off, and the generated script
    fails the job if Blender did not enable what was asked for (#238).

    Raises:
        ValueError: If the target index does not name a usable card.
    """
    logger.info(
        f"[Job {job_id}] Configuring job for GPU rendering. "
        f"Available backends: {detected_gpus}"
    )
    chosen_backend = gpu_device_script.choose_backend(detected_gpus)

    if chosen_backend:
        target_index = _resolve_target_gpu_index(gpu_index_override)
        if target_index is not None:
            gpu_device_script.append_isolated_gpu_script(
                script_lines, job_id, target_index
            )
        else:
            gpu_device_script.append_combined_gpu_script(
                script_lines, job_id, chosen_backend
            )
        # The emitted selection also sets cycles.device = 'GPU' inside its
        # fail-closed guard.
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

    For GPU jobs, it can isolate one physical card via
    `gpu_index_override` (split mode) or the `SETHLANS_FORCE_GPU_INDEX`
    configuration setting; the card is selected in Blender by its backend
    and device id, not by position. Without an index every listed card
    is used through the preferred backend. Either way the script verifies
    Blender's device state and exits with
    gpu_device_script.GPU_CHECK_EXIT_CODE before rendering if it is not
    what was asked for (#238).

    Args:
        job_id (int): The ID of the job for logging purposes.
        render_engine (str): The render engine (e.g., 'CYCLES').
        render_device (str): The render device ('CPU', 'GPU', 'ANY').
        render_settings (dict): User-defined settings to override.
        gpu_index_override (int, optional): A physical GPU index (a
            position in system_monitor.get_gpu_device_details()).
            Takes precedence over FORCE_GPU_INDEX. Defaults to None.
        is_cpu_fallback (bool): If True, forces CPU device config,
            overriding an 'ANY' preference. Defaults to False.

    Returns:
        str: The complete Python script as a string.

    Raises:
        ValueError: If render_engine is not in the allowlist, or a GPU
            job's target index does not name a usable detected card.
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

    script_lines = ["import bpy", ENGINE_HELPER]

    # Set the render engine FIRST to ensure the context is correct.
    script_lines.append(engine_call("bpy.context.scene.render", render_engine))

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
            _append_user_setting(script_lines, key, value)

    return "\n".join(script_lines)


def _append_user_setting(script_lines, key, value):
    """Append the line(s) that apply one user setting inside the scene loop.

    ``render.engine`` with a known engine name (the manager adds it to every
    tile job and animation frame) goes through the engine helper, like the
    engine line at the top (#242); any other value is assigned directly and
    still raises when Blender rejects it.

    ``cycles.feature_set`` is a named exception (#241): Blender 5.x's Cycles
    settings no longer have it, while 4.x still does, and the manager sends
    it for every Cycles tile job and animation frame. It is applied only
    when the scene's Cycles settings have it. Every other key is assigned
    directly, so an unknown setting still raises.
    """
    if key == 'render.engine' and is_known_engine(value):
        script_lines.append(f"    {engine_call('scene.render', value)}")
        return
    py_value = repr(value)
    if key != _FEATURE_SET_KEY:
        script_lines.append(f"    scene.{key} = {py_value}")
        return
    script_lines.append("    if hasattr(scene.cycles, 'feature_set'):")
    script_lines.append(f"        scene.cycles.feature_set = {py_value}")
    script_lines.append("    else:")
    script_lines.append(
        "        print(f'Skipping cycles.feature_set for scene {scene.name!a}: "
        "this Blender version has no such setting.')"
    )
