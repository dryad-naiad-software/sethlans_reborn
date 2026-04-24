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

# Hard ceiling on how long ``populate_cache`` is allowed to wait on
# the parser before giving up and marking the cache ready with whatever
# (possibly empty) series list it already had. Prevents a stalled
# download.blender.org connection from holding the daemon thread open
# indefinitely (issue #113). Module-level so tests can patch it.
POPULATE_CACHE_BUDGET_SECONDS: float = 60.0


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


def _populate_cache_body():
    """Actual fetch + store logic. Split out from :func:`populate_cache`
    so the outer wrapper can enforce an overall time budget via
    ``Thread.join(timeout=...)`` — the parser's per-request timeouts
    alone aren't enough because the parser loops over dozens of URLs.
    """
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


def populate_cache():
    """Fetch available Blender series with a hard wall-clock budget.

    Runs :func:`_populate_cache_body` on a short-lived inner daemon
    thread and waits up to :data:`POPULATE_CACHE_BUDGET_SECONDS` for
    it to finish. If the budget is exceeded (e.g. download.blender.org
    stalls mid-scrape — see issue #113), this function marks the
    cache ready with whatever it already has and returns cleanly.
    The inner thread is left running as a daemon; it will not block
    interpreter shutdown.

    Callers (``WorkersConfig.ready`` background thread, the refresh
    trigger) depend on this function returning in bounded time.
    """
    global _cache_ready
    worker = threading.Thread(
        target=_populate_cache_body,
        name='blender-series-cache-populate-inner',
        daemon=True,
    )
    worker.start()
    worker.join(timeout=POPULATE_CACHE_BUDGET_SECONDS)
    if worker.is_alive():
        _safe_log(
            'info',
            "Blender series cache populate exceeded %.1fs budget; "
            "marking cache ready without updated data (issue #113).",
            POPULATE_CACHE_BUDGET_SECONDS,
        )
        with _cache_lock:
            # Same contract as the exception branch in the body:
            # release any waiters, keep whatever was cached before.
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
