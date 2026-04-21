# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
In-memory cache of available Blender series from download.blender.org.

Populated on manager startup via a background thread, and refreshed
when the settings page is visited. If the refresh fails, the cached
data from startup is still served.
"""

import logging
import threading

from .blender_release_parser import get_blender_releases

logger = logging.getLogger(__name__)

_cache_lock = threading.Lock()
_cached_series: list[str] = []
_cache_ready: bool = False
_refresh_in_progress: bool = False


def _safe_log(level, msg, *args):
    """Log, swallowing ``ValueError`` / ``OSError`` from closed streams.

    The background populate thread is a daemon; when the interpreter
    tears down (e.g. pytest closing stdout between session teardown
    and the thread's next emit), the logging handlers may raise
    ``ValueError: I/O operation on closed file``. That is cosmetic
    noise — the thread is exiting anyway — so we suppress it rather
    than splattering tracebacks over otherwise-clean test output.
    """
    try:
        if level == 'info':
            logger.info(msg, *args)
        elif level == 'exception':
            logger.exception(msg, *args)
    except (ValueError, OSError):
        pass


def populate_cache():
    """Fetch available Blender series and store in the module-level cache."""
    global _cached_series, _cache_ready
    try:
        releases = get_blender_releases()
        series = sorted(
            {".".join(v.split(".")[:2]) for v in releases},
            key=lambda s: [int(p) for p in s.split(".")],
        )
        with _cache_lock:
            _cached_series = series
            _cache_ready = True
        _safe_log('info', "Blender series cache populated: %s", series)
    except Exception:
        _safe_log('exception', "Failed to populate Blender series cache")
        with _cache_lock:
            # Mark ready so endpoint doesn't appear stuck, but preserve
            # any previously cached data rather than overwriting with [].
            _cache_ready = True


def get_available_series() -> dict:
    """Return the cached series list and readiness flag."""
    with _cache_lock:
        return {
            "series": list(_cached_series),
            "cache_ready": _cache_ready,
        }


def trigger_background_refresh():
    """Spawn a background thread to refresh the cache (debounced)."""
    global _refresh_in_progress
    with _cache_lock:
        if _refresh_in_progress:
            return
        _refresh_in_progress = True

    def _refresh():
        global _refresh_in_progress
        try:
            populate_cache()
        finally:
            with _cache_lock:
                _refresh_in_progress = False

    thread = threading.Thread(target=_refresh, daemon=True)
    thread.start()
