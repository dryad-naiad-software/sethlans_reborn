# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Has the render written its first output file? (render per card FR-PC-ATT-2.)

``FirstOutput(folder).seen(now)`` is checked from the runner's tick, at most every
``CHECK_SECONDS``; once a file is in ``folder`` (frame files lie directly in ``output\\``,
``output.py``) it stays ``True`` for the launch, and ``on_seen`` (if any) is called once: the
claim's start-up is over (FR-PC-SU-1; a failure there is logged and never ends the render). The job's card memory figures
(``vram_job_peak_mb``, ``vram_job_other_cards_mb``) count from then on. No folder (not a render
launch): never seen. A folder that cannot be listed is logged once and counts as no file yet, so
the figures stay unknown rather than covering the start-up.
"""
import logging
import os
from typing import Callable, Optional

from sethlans_worker_agent.project_archive import fs_safe

logger = logging.getLogger(__name__)

CHECK_SECONDS = 2.0


class FirstOutput:
    """One launch's watch; the job thread only."""

    def __init__(self, folder=None, every_s: float = CHECK_SECONDS, on_seen: Optional[Callable[[], None]] = None):
        self._folder = folder
        self._on_seen = on_seen
        self._every = every_s
        self._seen = False
        self._checked_at: Optional[float] = None
        self._logged = False

    def seen(self, now: float) -> bool:
        if self._folder is None or self._seen:
            return self._seen
        if self._checked_at is not None and now - self._checked_at < self._every:
            return False
        self._checked_at = now
        try:
            with os.scandir(fs_safe.long_path(self._folder)) as entries:
                self._seen = any(entry.is_file(follow_symlinks=False) for entry in entries)
        except FileNotFoundError:
            return False
        except OSError as exc:
            if not self._logged:
                self._logged = True
                logger.warning("Could not list the render output folder %s: %s", self._folder, exc)
            return False
        if self._seen:
            self._notify()
        return self._seen

    def _notify(self) -> None:
        if self._on_seen is None:
            return
        try:
            self._on_seen()
        except Exception:  # noqa: BLE001 - the release clears the start-up anyway
            logger.exception("Could not mark the Unreal render's start-up as over.")
