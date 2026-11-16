# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
GPU drift (hardware change) detection helpers.

Owns:

- ``detect_physical_gpus_now()``: an uncached detection that returns the
  ordered (Blender device id, backend type) pair of every physical card,
  for comparison with the list the worker uses (#239; see gpu_list.py).
  It runs with the Blender that produced that list. When the version sync
  has removed it, another installed Blender is used (newest first, the same
  series first); if its list matches the list in use it is adopted as the
  detection Blender (``adopt_detection_executable``), otherwise the change
  is handled as a hardware change (#253);
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
import re
import subprocess
from pathlib import Path
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


# What ``WorkerCapacity.assert_gpu_list_unchanged`` returns when the fresh
# detection failed ("try again later"): a re-check request it answered stays
# pending (recheck.run_gpu_list_check).
DETECTION_FAILED = 'detection_failed'

# Per missing remembered executable, what was logged once: the switch to a
# fallback Blender, or that no usable Blender was found. Read and written only
# by the agent-loop thread (the drift check's only caller). _NOT_WARNED (not
# None) means "nothing logged", so a missing record (None) is reported too.
_NOT_WARNED = object()
_fallback_logged: object = _NOT_WARNED
_missing_exe_warned: object = _NOT_WARNED
# Another installed Blender the last detection ran with because the
# remembered one is gone; adopted when its list matches the list in use.
_fallback_exe: Optional[str] = None


def reset_missing_executable_warning() -> None:
    """Forget the once-only logs and the fallback. Test-only hook."""
    global _missing_exe_warned, _fallback_logged, _fallback_exe
    _missing_exe_warned = _fallback_logged = _NOT_WARNED
    _fallback_exe = None


def _version_key(version) -> Tuple[int, ...]:
    return tuple(int(part) if part.isdigit() else 0 for part in str(version).split('.'))


def installed_version_of(executable) -> Optional[str]:
    """The version of a managed Blender, from its ``blender-<version>-<platform>`` folder.

    The detection Blender's version is kept this way, as part of its stored
    executable path (``hardware_detection._gpu_details_blender_exe``).
    """
    for part in reversed(Path(str(executable or '')).parts):
        match = re.fullmatch(r'blender-(\d+(?:\.\d+)*)-.+', part)
        if match:
            return match.group(1)
    return None


def _choose_fallback_blender(remembered_version) -> Optional[str]:
    """An installed Blender to detect with: newest first, the remembered series first.

    Entries whose executable is missing are skipped. The startup detection's
    own choice is not affected.
    """
    from sethlans_worker_agent.tool_manager import tool_manager_instance
    versions = sorted({b['version'] for b in tool_manager_instance.scan_for_local_blenders()},
                      key=_version_key, reverse=True)
    series = _version_key(remembered_version)[:2] if remembered_version else None
    versions.sort(key=lambda version: _version_key(version)[:2] != series)  # stable: same series first
    for version in versions:
        exe = tool_manager_instance.get_blender_executable_path(version)
        if exe and os.path.isfile(exe):
            return exe
    return None


def _detection_executable() -> Optional[str]:
    """Return the Blender to run the fresh detection with, or None (skip).

    Normally the Blender that produced the cached GPU list: another version
    can report a card under another id or backend, which would read as a
    false GPU change. When that one is gone (the version sync removes old
    versions), a fallback is chosen (``_choose_fallback_blender``); if its
    list matches the list in use, ``adopt_detection_executable`` makes it the
    remembered one (#253). Without a usable Blender: None, "try again later".
    """
    global _missing_exe_warned, _fallback_logged, _fallback_exe
    from sethlans_worker_agent import hardware_detection
    _fallback_exe = None
    exe = hardware_detection._gpu_details_blender_exe
    if exe and os.path.isfile(exe):
        _missing_exe_warned = _fallback_logged = _NOT_WARNED
        return exe
    other = _choose_fallback_blender(installed_version_of(exe))
    if other:
        if _fallback_logged != exe:
            logger.info("The Blender that produced the GPU list in use (%s) is no longer installed; "
                        "detecting with %s.", ascii(exe), ascii(other))
            _fallback_logged = exe
        _fallback_exe = other
        return other
    if _missing_exe_warned != exe:
        logger.warning("detect_physical_gpus_now: no usable Blender installation found to detect the GPUs "
                       "with (the one that produced the list in use was %s); skipping drift check.", ascii(exe))
        _missing_exe_warned = exe
    return None


def adopt_detection_executable() -> None:
    """Remember the fallback Blender of the last detection; it matched the list in use."""
    global _fallback_exe, _missing_exe_warned, _fallback_logged
    if _fallback_exe is None:
        return
    from sethlans_worker_agent import hardware_detection
    with hardware_detection._gpu_details_lock:
        hardware_detection._gpu_details_blender_exe = _fallback_exe
    logger.info("GPU check: %s reports the same GPU list; using it for the check from now on.",
                ascii(_fallback_exe))
    _fallback_exe = None
    _missing_exe_warned = _fallback_logged = _NOT_WARNED


def detect_physical_gpus_now() -> Optional[List[Tuple[object, object]]]:
    """Uncached GPU list for drift detection.

    Spawns a fresh Blender detection subprocess via detect_gpus.py, using
    the Blender executable that produced the cached list in use (or, once
    it is gone, another installed one; see ``_detection_executable``), and
    returns one (Blender device id, backend type) pair per preferred
    physical card, in the order ``_filter_preferred_gpus`` returns them.
    MUST NOT consult or update hardware_detection module caches
    (_gpu_devices_cache, _gpu_details_cache). Costs ~5-15 seconds per
    call; FR-23 governs how often the drift check may invoke it.

    Return value semantics:
      * a list (possibly empty) — successful detection.
      * ``None`` — the detection failed (no Blender installed, the
        subprocess failed, timed out or could not be started, or its output
        could not be decoded or parsed).
        Callers MUST treat this as "skip this check, try again later"
        and NOT as "no GPUs detected." A subprocess hiccup must not
        trigger a false drift event.
    """
    from sethlans_worker_agent.capacity.gpu_list import gpu_identity
    # Imported lazily to avoid a circular import at package load time.
    from sethlans_worker_agent.hardware_detection import _filter_preferred_gpus

    blender_exe = _detection_executable()
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
        result = subprocess.run(  # Blender's Python writes UTF-8 whatever the locale
            command, capture_output=True, text=True, encoding='utf-8', errors='replace', check=True, timeout=90,
            env=env_cleanup.clean_env_for_blender(),
        )
    except (subprocess.SubprocessError, OSError, ValueError) as exc:  # ValueError: undecodable output
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
            try:
                return gpu_identity(_filter_preferred_gpus(raw_devices))
            except (KeyError, TypeError, AttributeError, ValueError) as exc:  # a malformed record
                logger.warning("detect_physical_gpus_now: unusable device list (%s); skipping drift check.", exc)
                return None

    logger.warning(
        "detect_physical_gpus_now: could not parse detect_gpus.py output; "
        "skipping drift check."
    )
    return None
