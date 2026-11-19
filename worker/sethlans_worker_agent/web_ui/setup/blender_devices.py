# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The Blender device check that follows the wizard's Blender step (FR-CW-2).

The wizard's card step never starts Blender; it joins the OS card list with
the cached Blender device list. When the Blender step completes (downloaded,
or already installed), ``start_device_check()`` fills that cache once on a
background thread, so the card step can show which cards Blender offers.
``hardware_detection.get_gpu_device_details`` serialises the fill under its
own lock, so a check that overlaps the agent's own detection runs Blender once.

A detection failure that ``hardware_detection`` handles itself caches an
empty list, which the card step shows as ``failed``. An unexpected exception
escaping the detection is logged here and recorded in ``_failed``, so the
step also shows ``failed`` instead of waiting for a check that never comes;
nothing is cached then, so a later start retries (and clears the flag).

``_lock`` guards ``_thread`` and ``_failed`` only and is never held while
Blender runs.
"""
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_thread: Optional[threading.Thread] = None
_failed = False


def _run() -> None:
    global _failed
    from sethlans_worker_agent import hardware_detection
    try:
        hardware_detection.get_gpu_device_details()
    except Exception:  # a background thread must not die with a traceback; the card step shows "failed"
        logger.warning("The Blender device check after the Blender step failed.", exc_info=True)
        with _lock:
            _failed = True


def _start() -> bool:
    """Start the check unless the cache is filled or a check runs; True when one was started."""
    global _thread, _failed
    from sethlans_worker_agent import hardware_detection
    if hardware_detection._gpu_details_cache is not None:
        return False
    with _lock:
        if _thread is not None and _thread.is_alive():
            return False
        _failed = False
        _thread = threading.Thread(target=_run, name='setup-blender-device-check', daemon=True)
        _thread.start()
    logger.info("Setup wizard: checking which graphics cards Blender offers.")
    return True


def start_device_check() -> None:
    """Fill the Blender device cache in the background, once (called when the Blender step completes)."""
    _start()


def check_state() -> Optional[str]:
    """``'running'``, ``'failed'`` (the last check raised) or ``None``, read in one critical section."""
    with _lock:
        if _thread is not None and _thread.is_alive():
            return 'running'
        return 'failed' if _failed else None
