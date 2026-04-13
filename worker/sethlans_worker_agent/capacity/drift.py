# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
GPU drift detection helpers.

Owns the uncached physical-GPU count helper for drift detection and
the module-level exit-code flag set by the drift handler. The actual
drift handler flow (terminate active jobs + set shutdown event) lives
in WorkerCapacity.assert_gpu_count_unchanged() in slots.py.

FR-22: sys.exit and os._exit are FORBIDDEN inside this module. The
top-level epilogue in agent.main (or run_worker.py) reads the flag
and calls sys.exit after the graceful shutdown path runs.
"""
import json
import logging
import os
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)

# Set by the drift handler. Read by the agent.main epilogue to decide
# the process exit code. NEVER call sys.exit from inside this module.
_drift_detected_exit_code: Optional[int] = None


def get_drift_exit_code() -> Optional[int]:
    """Return the drift exit code (None if no drift has been detected)."""
    return _drift_detected_exit_code


def set_drift_exit_code(code: int) -> None:
    """Set the drift exit code. Called by the drift handler."""
    global _drift_detected_exit_code
    _drift_detected_exit_code = code


def reset_drift_exit_code() -> None:
    """Reset the drift exit code. Test-only hook."""
    global _drift_detected_exit_code
    _drift_detected_exit_code = None


def count_physical_gpus_now() -> Optional[int]:
    """Uncached physical-GPU count for drift detection.

    Spawns a fresh Blender detection subprocess via detect_gpus.py and
    returns the count of preferred physical devices. MUST NOT consult
    or update hardware_detection module caches (_gpu_devices_cache,
    _gpu_details_cache). Costs ~5-15 seconds per call; FR-23 governs
    how often the drift check may invoke it.

    Return value semantics:
      * ``int >= 0`` — successful detection, the integer is the real count.
      * ``None`` — the detection subprocess failed (missing Blender,
        CalledProcessError, TimeoutExpired, or unparseable output).
        Callers MUST treat this as "skip this check, try again later"
        and NOT as "zero GPUs detected." A subprocess hiccup must not
        trigger a false drift event.
    """
    # Imported lazily to avoid a circular import at package load time.
    from sethlans_worker_agent.hardware_detection import (
        _filter_preferred_gpus,
        _find_any_blender_executable,
    )

    blender_exe = _find_any_blender_executable()
    if not blender_exe:
        logger.warning(
            "count_physical_gpus_now: no Blender executable available; "
            "skipping drift check."
        )
        return None

    script_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        'utils', 'detect_gpus.py',
    )
    command = [
        blender_exe, '--background', '--factory-startup',
        '--python', script_path,
    ]
    try:
        from sethlans_worker_agent import env_cleanup
        result = subprocess.run(
            command, capture_output=True, text=True, check=True, timeout=90,
            env=env_cleanup.clean_env_for_blender(),
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        logger.warning(
            "count_physical_gpus_now: Blender detection subprocess "
            "failed (%s); skipping drift check.", exc,
        )
        return None

    for line in result.stdout.strip().splitlines():
        stripped = line.strip()
        if stripped.startswith('[') and stripped.endswith(']'):
            try:
                raw_devices = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            return len(_filter_preferred_gpus(raw_devices))

    logger.warning(
        "count_physical_gpus_now: could not parse detect_gpus.py output; "
        "skipping drift check."
    )
    return None
