# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The card probe shared by the worker's two card GETs (FR-CW-2, FR-CW-4).

``cached_probe()`` serves a probe that finished at most ``CACHE_SECONDS``
ago. At most one probe runs at a time, under ``_probe_lock``; a caller that
arrives while a probe runs waits for it and gets its result, so a probe
slower than ``CACHE_SECONDS`` (``system_profiler`` on macOS) is not run back
to back. A cached probe is also dropped when the Blender device cache it was
joined with has changed (the wizard's device check finished, or the agent
adopted a new detection), so the page never shows "not checked" after the
check is done. ``fresh_probe()`` always probes (the POSTs) and refreshes the
cache.

``_probe_lock`` is a leaf: nothing else is taken while it is held except
what the probe itself takes (none of the worker's locks).
"""
import threading
import time
from typing import Optional, Tuple

from sethlans_worker_agent.web_ui import cards_view

CACHE_SECONDS = 5.0

_probe_lock = threading.Lock()
# (finished, Blender cache token, probe); under _probe_lock
_cache: Optional[Tuple[float, tuple, cards_view.Probe]] = None


def _blender_token() -> tuple:
    """The Blender device cache as it is now: the list object itself and its executable.

    The list is kept by reference and compared with ``is``, so a replaced
    list is always seen as a change (no id reuse).
    """
    from sethlans_worker_agent import hardware_detection
    return hardware_detection._gpu_details_cache, hardware_detection._gpu_details_blender_exe


def _same_token(left: tuple, right: tuple) -> bool:
    return left[0] is right[0] and left[1] == right[1]


def _probe_locked() -> cards_view.Probe:
    """Run one probe and cache it; caller holds ``_probe_lock``."""
    global _cache
    token = _blender_token()  # before the probe: a change during it drops the result at the next call
    probe = cards_view.probe_now()
    _cache = (time.monotonic(), token, probe)
    return probe


def cached_probe() -> cards_view.Probe:
    """A probe at most ``CACHE_SECONDS`` old, or the one that was running when this caller arrived."""
    arrived = time.monotonic()
    with _probe_lock:
        if _cache is not None:
            finished, token, probe = _cache
            fresh = finished >= arrived or time.monotonic() - finished <= CACHE_SECONDS
            if fresh and _same_token(token, _blender_token()):
                return probe
        return _probe_locked()


def fresh_probe() -> cards_view.Probe:
    """A new probe (for a save), which also refreshes the cache."""
    with _probe_lock:
        return _probe_locked()


def reset_for_tests() -> None:
    """Forget the cached probe. Test-only hook."""
    global _cache
    with _probe_lock:
        _cache = None
