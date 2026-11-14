# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Decision state of the UE process runner's monitor loop (Unreal foundation
FR-UE-11): fatal log rules, the success marker and quit grace, log flood,
per-tick conflicts, cancel causes, shutdown, the wall timeout and hang.
"""
import threading
from typing import List, Optional

from sethlans_worker_agent.unreal_executor.log_validation import LOG_FLOOD, LogScan, scan_line
from sethlans_worker_agent.unreal_executor.runner_monitor import CpuTracker, FloodTracker
from sethlans_worker_agent.unreal_executor.runner_types import HANG_CPU_SECONDS, HANG_CPU_WINDOW_SECONDS, RunSpec


class MonitorState:
    """Per-launch decisions of the monitor loop (FR-UE-11 order: fatal, flood, conflict, cancel,
    shutdown, timeout, hang, quit grace)."""

    def __init__(self, spec: RunSpec, started: float):
        self.spec = spec
        self.started = started
        self.deadline = started + spec.timeout_s
        self.last_growth = started
        self.marker_at: Optional[float] = None
        self.fatal_rule = ''
        self.detail = ''
        self.scan = LogScan()
        self.rules = tuple(spec.fatal_rules) + tuple(r for r in spec.count_rules if r not in spec.fatal_rules)
        self.cpu = CpuTracker(spec.excluded_images)
        self.flood = FloodTracker(spec.log_max_bytes, spec.log_flood_bytes_per_min)
        self.waiter = spec.shutdown_event or threading.Event()

    def lines(self, lines: List[str], now: float) -> Optional[str]:
        if lines:
            self.last_growth = now
        for text in lines:
            for rule in scan_line(text, self.scan, self.rules):
                if rule.severity == 'fatal' and not self.fatal_rule:
                    self.fatal_rule, self.detail = rule.name, text[:500]
            if (self.marker_at is None and self.spec.success_marker is not None
                    and self.spec.success_marker.search(text)):
                self.marker_at = now
        return 'fatal' if self.fatal_rule else None

    def _flood(self, now: float) -> Optional[str]:
        message = self.flood.check((self.spec.log_path, self.spec.stdout_path), now)
        if not message:
            return None
        self.scan.counts[LOG_FLOOD] = self.scan.counts.get(LOG_FLOOD, 0) + 1
        self.detail = message
        if self.marker_at is not None:
            return 'exited_after_grace'
        self.fatal_rule = LOG_FLOOD
        return 'fatal'

    def decide(self, now: float) -> Optional[str]:
        spec = self.spec
        if self.marker_at is None and spec.success_check is not None and spec.success_check():
            self.marker_at = now
        reason = self._flood(now)
        if reason:
            return reason
        conflict = spec.tick_check() if spec.tick_check is not None else None
        if conflict:
            self.detail = conflict
            return 'local_conflict'
        cause = spec.cancel() if spec.cancel is not None else None
        if cause:
            return cause
        if spec.shutdown_event is not None and spec.shutdown_event.is_set():
            return 'shutdown'
        if now >= self.deadline:
            return 'timeout'
        if now - self.last_growth >= spec.hang_s:
            cpu = self.cpu.cpu_over(HANG_CPU_WINDOW_SECONDS, now)
            if cpu is not None and cpu < HANG_CPU_SECONDS:
                self.detail = (f"no log output for {int(now - self.last_growth)} s and {cpu:.1f} CPU-s "
                               f"in the last {int(HANG_CPU_WINDOW_SECONDS)} s")
                return 'hang'
        if self.marker_at is not None and now - self.marker_at >= spec.quit_grace_s:
            return 'exited_after_grace'
        return None
