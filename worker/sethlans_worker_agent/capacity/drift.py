# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
GPU drift (hardware change) detection helpers.

Owns:

- ``detect_physical_gpus_now()``: an uncached detection that returns the
  ordered (Blender device id, backend type) pair of every physical card,
  for comparison with the list the worker uses (#239; see gpu_list.py);
- the module-level exit-code flag set by the drift handler.

When the comparison runs (periodically, and early after a failed GPU
device check) is decided in recheck.py.

The drift handler flow (compare, terminate active jobs, set the shutdown
event) lives in WorkerCapacity.assert_gpu_list_unchanged() in slots.py;
the cadence lives in job_processor.maybe_assert_gpu_list_unchanged().

FR-22: sys.exit and os._exit are FORBIDDEN inside this module. The
top-level epilogue in agent.main (or run_worker.py) reads the flag
and calls sys.exit after the graceful shutdown path runs.
"""
import json
import logging
import os
import subprocess
from typing import List, Optional, Tuple

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


# Executable whose absence was last logged at WARNING, so a missing
# Blender is reported once per occurrence, not on every interval. Read and
# written only by the agent-loop thread (the drift check's only caller).
# _NOT_WARNED (not None) means "nothing logged", so a missing record
# (None) is reported too.
_NOT_WARNED = object()
_missing_exe_warned: object = _NOT_WARNED


def reset_missing_executable_warning() -> None:
    """Forget the last missing-executable warning. Test-only hook."""
    global _missing_exe_warned
    _missing_exe_warned = _NOT_WARNED


def _startup_detection_executable() -> Optional[str]:
    """Return the Blender that produced the cached GPU list, if it still exists.

    The fresh list must come from the same Blender as the list in use: a
    different version can report a card under another id or backend, which
    would read as a false GPU change. None means "skip, try again later".
    """
    global _missing_exe_warned
    from sethlans_worker_agent import hardware_detection
    exe = hardware_detection._gpu_details_blender_exe
    if exe and os.path.isfile(exe):
        _missing_exe_warned = _NOT_WARNED
        return exe
    if exe != _missing_exe_warned:
        _missing_exe_warned = exe
        logger.warning(
            "detect_physical_gpus_now: the Blender that produced the GPU list "
            "in use (%s) is no longer available; skipping drift check until "
            "it is.", ascii(exe),
        )
    else:
        logger.debug("detect_physical_gpus_now: detection Blender still missing.")
    return None


def detect_physical_gpus_now() -> Optional[List[Tuple[object, object]]]:
    """Uncached GPU list for drift detection.

    Spawns a fresh Blender detection subprocess via detect_gpus.py, using
    the same Blender executable that produced the cached list in use, and
    returns one (Blender device id, backend type) pair per preferred
    physical card, in the order ``_filter_preferred_gpus`` returns them.
    MUST NOT consult or update hardware_detection module caches
    (_gpu_devices_cache, _gpu_details_cache). Costs ~5-15 seconds per
    call; FR-23 governs how often the drift check may invoke it.

    Return value semantics:
      * a list (possibly empty) — successful detection.
      * ``None`` — the detection subprocess failed (that Blender missing,
        CalledProcessError, TimeoutExpired, or unparseable output).
        Callers MUST treat this as "skip this check, try again later"
        and NOT as "no GPUs detected." A subprocess hiccup must not
        trigger a false drift event.
    """
    from sethlans_worker_agent.capacity.gpu_list import gpu_identity
    # Imported lazily to avoid a circular import at package load time.
    from sethlans_worker_agent.hardware_detection import _filter_preferred_gpus

    blender_exe = _startup_detection_executable()
    if not blender_exe:
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
            "detect_physical_gpus_now: Blender detection subprocess "
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
            return gpu_identity(_filter_preferred_gpus(raw_devices))

    logger.warning(
        "detect_physical_gpus_now: could not parse detect_gpus.py output; "
        "skipping drift check."
    )
    return None
