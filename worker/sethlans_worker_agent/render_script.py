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

from sethlans_worker_agent import gpu_device_script
from sethlans_worker_agent.cards.in_use import NONE_USABLE_TEXT
from sethlans_worker_agent.engine_script import ENGINE_HELPER, ENGINE_IDENTIFIERS, engine_call, is_known_engine
from sethlans_worker_agent.format_script import FILE_FORMAT_KEY, media_type_lines, order_settings

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

NO_CARD_IN_SLOT_TEXT = "This job's slot holds no graphics card."


def _uses_gpu(job_id, render_device, reservation) -> bool:
    """CPU or GPU from the reservation's ``device_used`` (FR-CB-1).

    A GPU job without a GPU reservation that carries Blender cards fails
    before Blender starts and never falls back to the CPU (FR-CB-3).
    """
    gpu = reservation is not None and reservation.device_used == 'GPU'
    if render_device == 'GPU' and not (gpu and reservation.job_cards):
        fallback = NO_CARD_IN_SLOT_TEXT if gpu else NONE_USABLE_TEXT  # a GPU slot with no card: exclusive
        problem = (reservation.problem if reservation is not None else '') or fallback
        logger.error(f"[Job {job_id}] GPU job with no graphics card in use: {problem}")
        raise ValueError(problem)
    return gpu


def generate_render_config_script(job_id, render_engine, render_device, render_settings, reservation=None):
    """
    Generates a Python script to configure Blender's render settings.

    This script is executed by Blender to ensure settings are applied
    before rendering. It supports overriding the render engine, device,
    and other user-defined settings.

    For Cycles, CPU or GPU follows the reservation's ``device_used``. A GPU
    reservation's cards are selected in Blender by backend and device id
    (gpu_device_script): its one card in split mode, every card in use in
    combined mode; every other entry is switched off. The script verifies
    Blender's device state and exits with
    gpu_device_script.GPU_CHECK_EXIT_CODE before rendering if it is not
    what was asked for (#238).

    Args:
        job_id (int): The ID of the job for logging purposes.
        render_engine (str): The render engine (e.g., 'CYCLES').
        render_device (str): The render device ('CPU', 'GPU', 'ANY').
        render_settings (dict): User-defined settings to override.
        reservation: The job's capacity.SlotReservation, or None (CPU).

    Returns:
        str: The complete Python script as a string.

    Raises:
        ValueError: If render_engine is not in the allowlist, or a GPU
            job has no graphics card in use (FR-CB-3), or a reservation
            card cannot be selected.
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
        if _uses_gpu(job_id, render_device, reservation):
            # Sets cycles.device = 'GPU' inside its fail-closed guard.
            gpu_device_script.append_gpu_script(script_lines, job_id, reservation)
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
        # Output-format keys in dependency order (#243, format_script.py).
        for key, value in order_settings(render_settings):
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

    ``render.image_settings.file_format`` is preceded by the Blender 5.x
    media type switch it needs (#243, format_script.py).

    ``cycles.feature_set`` is a named exception (#241): Blender 5.x has no
    such setting, and the manager sends it only for versions that have it or
    an unknown version (#244). It is applied only when the scene's Cycles
    settings have it (for an unknown version, or a key added by hand). Every
    other key is assigned directly, so an unknown setting still raises, and
    the failing script stops the render (``--python-exit-code``, #240).

    Values are embedded with ``ascii()`` so the script is pure ASCII
    whatever they contain (#240).
    """
    if key == 'render.engine' and is_known_engine(value):
        script_lines.append(f"    {engine_call('scene.render', value)}")
        return
    if key == FILE_FORMAT_KEY:
        script_lines.extend(media_type_lines(value))
    py_value = ascii(value)
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
