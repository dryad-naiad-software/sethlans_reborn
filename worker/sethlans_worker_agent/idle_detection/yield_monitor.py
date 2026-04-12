# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Yield detection during active rendering (FR-4, FR-5).

The YieldMonitor watches for artist-return signals while the worker
has a Blender subprocess running. It checks fast-path triggers
(GPU contention, creative app launch, session unlock) and the slow-path
(sustained input activity for 90s). Schedule window closure is also
checked each poll.

Thread safety contract:
- YieldMonitor writes ``_reason`` and ``_progress`` BEFORE calling
  ``_yield_event.set()``.
- ``Event.set()`` provides ordering via its internal Lock.
- The executor reads ``get_reason()`` only AFTER ``yield_event.is_set()``
  returns True, guaranteeing it sees the written values.
"""
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Optional

import psutil

from sethlans_worker_agent.idle_detection.contention import (
    check_gpu_contention_per_process,
)
from sethlans_worker_agent.idle_detection.input_monitor import (
    get_seconds_since_last_input,
)
from sethlans_worker_agent.idle_detection.schedule import (
    is_inside_claim_window,
)
from sethlans_worker_agent.idle_detection.session_win32 import (
    session_unlock_event,
)
from sethlans_worker_agent.idle_detection.progress_parser import (
    parse_blender_progress,
)
from sethlans_worker_agent import blender_executor

logger = logging.getLogger(__name__)

# Valid pattern for creative app names (FR-4b validation).
_APP_NAME_RE = re.compile(r"^[a-zA-Z0-9._-]+$")

# Yield poll interval in seconds.
_POLL_INTERVAL = 2

# Slow-path: gap that resets the presence timer.
_SLOW_PATH_GAP_SECONDS = 10


@dataclass
class YieldReason:
    """Describes why the worker yielded a render."""
    reason: str       # e.g. "artist_return_gpu_contention"
    progress: float   # 0.0-1.0 at trigger time


class YieldMonitor:
    """Monitors for artist-return signals while the worker is rendering.

    Instantiated when a render starts; stopped when the render ends or
    yield is triggered. Runs its monitoring loop on a daemon thread.
    """

    def __init__(
        self,
        blender_pid: int,
        job_id: int,
        render_engine: str,
        manual_stop_event: threading.Event,
        shutdown_event: threading.Event,
    ):
        self._blender_pid = blender_pid
        self._job_id = job_id
        self._render_engine = render_engine
        self._manual_stop_event = manual_stop_event
        self._shutdown_event = shutdown_event

        self._yield_event = threading.Event()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._triggered = False
        self._reason: Optional[YieldReason] = None
        self._render_start_time = time.monotonic()

        # Frozen config at construction time
        from sethlans_worker_agent import config
        self._creative_apps = _validated_creative_apps(
            config.IDLE_CREATIVE_APP_NAMES
        )
        self._slow_path_threshold = config.IDLE_SLOW_PATH_THRESHOLD_SECONDS

    @property
    def yield_event(self) -> threading.Event:
        """The event set when a yield trigger fires."""
        return self._yield_event

    def start(self) -> None:
        """Start the monitoring daemon thread."""
        self._thread = threading.Thread(
            target=self._monitor_loop,
            name=f"yield-monitor-{self._job_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the monitor. MUST be called in the job thread's finally block."""
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5)

    def get_reason(self) -> Optional[YieldReason]:
        """Return the yield reason. Only valid after yield_event.is_set()."""
        return self._reason

    def _fire_trigger(self, reason_str: str) -> None:
        """Set the trigger with write-before-signal ordering."""
        progress = self._get_current_progress()
        self._reason = YieldReason(reason=reason_str, progress=progress)
        self._triggered = True
        self._yield_event.set()
        logger.info(
            "Yield triggered: %s (progress=%.2f) for job %s",
            reason_str, progress, self._job_id,
        )

    def _get_current_progress(self) -> float:
        """Get render progress from Blender stdout."""
        line = blender_executor.get_last_output_line(self._job_id)
        if not line:
            return 0.0
        progress = parse_blender_progress(line, self._render_engine)
        return progress if progress is not None else 0.0

    def _monitor_loop(self) -> None:
        """Polling loop: checks triggers every 2 seconds."""
        # Slow-path state
        presence_start: Optional[float] = None
        last_input_detected: float = 0.0

        while not self._stop_event.is_set() and not self._shutdown_event.is_set():
            if self._triggered:
                break

            # 1. Schedule window close check (re-reads config each poll)
            self._check_schedule_window()
            if self._triggered:
                break

            # 2. Fast-path checks
            self._check_gpu_contention()
            if self._triggered:
                break

            self._check_creative_app_launch()
            if self._triggered:
                break

            self._check_session_unlock()
            if self._triggered:
                break

            # 3. Slow-path: sustained input activity
            presence_start, last_input_detected = self._check_slow_path(
                presence_start, last_input_detected,
            )
            if self._triggered:
                break

            self._stop_event.wait(_POLL_INTERVAL)

    def _check_schedule_window(self) -> None:
        """Check if the schedule window just closed (FR-8g).

        Skips the check if less than 30 seconds have elapsed since
        render start to avoid killing jobs that were legitimately
        claimed at the boundary (e.g. 06:59:59 with window closing
        at 07:00:00).
        """
        elapsed = time.monotonic() - self._render_start_time
        if elapsed < 30:
            return
        from sethlans_worker_agent import config
        window_cfg = config.get_schedule_config()
        if not window_cfg.get("enabled", False):
            return
        if not is_inside_claim_window(window_cfg):
            self._fire_trigger("schedule_window_closed")

    def _check_gpu_contention(self) -> None:
        """Fast-path: GPU contention from non-Sethlans process (FR-4a)."""
        if check_gpu_contention_per_process(exclude_pid=self._blender_pid):
            self._fire_trigger("artist_return_gpu_contention")

    def _check_creative_app_launch(self) -> None:
        """Fast-path: creative application launched (FR-4b)."""
        if not self._creative_apps:
            return
        try:
            for proc in psutil.process_iter(["pid", "name"]):
                try:
                    pid = proc.info["pid"]
                    name = proc.info["name"]
                    if pid == self._blender_pid or not name:
                        continue
                    name_lower = name.lower()
                    for app in self._creative_apps:
                        if app in name_lower:
                            self._fire_trigger("artist_return_app_launch")
                            return
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception as exc:
            logger.debug("Creative app check error: %s", exc)

    def _check_session_unlock(self) -> None:
        """Fast-path: session unlock (FR-4c)."""
        if session_unlock_event.is_set():
            session_unlock_event.clear()
            self._fire_trigger("artist_return_session_unlock")

    def _check_slow_path(
        self,
        presence_start: Optional[float],
        last_input_detected: float,
    ) -> tuple:
        """Slow-path: sustained input activity (FR-5)."""
        idle_secs = get_seconds_since_last_input()
        if idle_secs is None:
            return presence_start, last_input_detected

        now = time.monotonic()

        if idle_secs < _SLOW_PATH_GAP_SECONDS:
            # User is active
            if presence_start is None:
                presence_start = now
            last_input_detected = now

            elapsed = now - presence_start
            if elapsed >= self._slow_path_threshold:
                self._fire_trigger("artist_return_sustained_activity")
        else:
            # Gap > 10s: reset the timer
            presence_start = None

        return presence_start, last_input_detected


def _validated_creative_apps(raw_list):
    """Validate and normalize creative app names (FR-4b)."""
    if not raw_list:
        return []
    validated = []
    for name in raw_list:
        name = str(name).strip()
        if not name:
            continue
        if not _APP_NAME_RE.match(name):
            logger.warning(
                "Rejecting creative_app_names entry %r: "
                "contains characters outside [a-zA-Z0-9._-].",
                name,
            )
            continue
        validated.append(name.lower())
    return validated
