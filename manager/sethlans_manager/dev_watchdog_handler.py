# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Filesystem event filter + debouncer for the dev watchdog (Phase 6).

Split from ``dev_watchdog.py`` to respect the 300-line ceiling. This
module holds the parts that do not depend on spawning subprocesses:

* ``_should_trigger_restart`` — path filter (``*.py`` only, skip
  ``__pycache__``/``.git``/``.venv``/``node_modules``).
* ``_DebouncedRestarter`` — coalesces a burst of events into exactly
  one restart fired when the 500 ms window elapses with no new events.
* ``_make_event_handler`` — builds the ``FileSystemEventHandler``
  subclass instance wired to a ``_DebouncedRestarter``.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 0.5
_WATCH_SUFFIX = ".py"
_IGNORED_DIR_PARTS = frozenset({
    "__pycache__",
    ".git",
    ".venv",
    ".venv-build",
    "node_modules",
})


def _should_trigger_restart(src_path: str) -> bool:
    """Return ``True`` iff a file event should debounce toward a restart.

    Filters to ``*.py`` only and rejects any path whose components
    include an ignored directory (``__pycache__``, ``.git``, ``.venv``,
    ``node_modules``).
    """
    if not src_path:
        return False
    path = Path(src_path)
    if path.suffix != _WATCH_SUFFIX:
        return False
    for part in path.parts:
        if part in _IGNORED_DIR_PARTS:
            return False
    return True


class _DebouncedRestarter:
    """Coalesces a burst of filesystem events into a single restart.

    On each qualifying event, the timer is (re)started for
    ``DEBOUNCE_SECONDS``. When it finally elapses with no new events,
    ``callback`` fires exactly once.
    """

    def __init__(self, callback, debounce_seconds: float = DEBOUNCE_SECONDS):
        self._callback = callback
        self._debounce = debounce_seconds
        self._lock = threading.Lock()
        self._timer: Optional[threading.Timer] = None

    def schedule(self) -> None:
        """Note a filesystem event and (re)start the debounce timer."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def cancel(self) -> None:
        """Drop any pending restart without firing it."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    def _fire(self) -> None:
        with self._lock:
            self._timer = None
        try:
            self._callback()
        except Exception:
            logger.exception("dev watchdog restart callback failed")


def _make_event_handler(restarter: _DebouncedRestarter):
    """Return a watchdog ``FileSystemEventHandler`` instance.

    Imported lazily so modules that merely reuse the debouncer or the
    path filter do not pull in ``watchdog`` at import time.
    """
    from watchdog.events import FileSystemEventHandler

    class _Handler(FileSystemEventHandler):
        def _maybe_schedule(self, event) -> None:
            if getattr(event, "is_directory", False):
                return
            src = getattr(event, "src_path", "") or ""
            dest = getattr(event, "dest_path", "") or ""
            if _should_trigger_restart(src) or (
                dest and _should_trigger_restart(dest)
            ):
                restarter.schedule()

        def on_created(self, event):
            self._maybe_schedule(event)

        def on_modified(self, event):
            self._maybe_schedule(event)

        def on_moved(self, event):
            self._maybe_schedule(event)

        def on_deleted(self, event):
            self._maybe_schedule(event)

    return _Handler()
