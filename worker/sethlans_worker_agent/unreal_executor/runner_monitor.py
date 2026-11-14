# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Monitor helpers for the UE process runner (Unreal foundation FR-UE-11).

* ``LogTail`` (``log_tail``, re-exported): new complete lines only, opened
  after the log appears, bounded line length.
* ``CpuTracker``: tree CPU as the sum of per-process CPU-time **deltas**
  keyed by ``(pid, create_time)``, accumulated monotonically (an exited
  ShaderCompileWorker never lowers it). Processes whose image equals a
  farm zenserver image are excluded (exact path, never a name prefix).
* ``FloodTracker``: the size cap and the growth-rate rule (any 60 s window)
  over the UE log and the stdout file (S3).
* ``Members``: the processes to watch: the Job Object's members (Windows)
  or the root and its descendants (fallback). A cached ``psutil.Process``
  is reused only while ``is_running()`` (psutil checks the create time),
  so a reused PID is never attributed to the old process (Conc L1).
  ``procs.json`` lists ``(pid, create_time, exe)``; it is cumulative across
  the phases of one job (seeded from the existing file, Conc L2), written
  atomically, and a failed write is logged and retried on the next tick
  instead of ending the phase (Conc L3).
"""
import collections
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Callable, Deque, Dict, Iterable, List, Optional, Tuple

import psutil

from sethlans_worker_agent.unreal_executor.log_tail import LogTail  # noqa: F401 - re-exported
from sethlans_worker_agent.unreal_executor.runner_types import FLOOD_WINDOW_SECONDS, MB

logger = logging.getLogger(__name__)

MAX_LEDGER_ENTRIES = 10000
MAX_LEDGER_BYTES = 4 * MB


def _image(proc) -> str:
    try:
        return os.path.normcase(proc.exe() or '')
    except (psutil.Error, OSError):
        return ''


class CpuTracker:

    def __init__(self, excluded_images: frozenset = frozenset()):
        self._excluded = frozenset(os.path.normcase(path) for path in excluded_images)
        self._last: Dict[Tuple[int, float], float] = {}
        self.total = 0.0
        self._history: Deque[Tuple[float, float]] = collections.deque()

    def update(self, procs: Iterable, now: float) -> None:
        for proc in procs:
            try:
                key = (proc.pid, proc.create_time())
                if _image(proc) in self._excluded:
                    continue
                times = proc.cpu_times()
                cpu = float(times.user) + float(times.system)
            except (psutil.Error, OSError):
                continue
            delta = cpu - self._last.get(key, 0.0)
            if delta > 0:
                self.total += delta
            self._last[key] = max(cpu, self._last.get(key, 0.0))
        self._history.append((now, self.total))

    def cpu_over(self, window_s: float, now: float) -> Optional[float]:
        """Tree CPU over the last ``window_s``; None until that much history exists."""
        boundary = now - window_s
        base = None
        while len(self._history) > 1 and self._history[1][0] <= boundary:
            self._history.popleft()
        if self._history and self._history[0][0] <= boundary:
            base = self._history[0][1]
        return None if base is None else self.total - base


class FloodTracker:

    def __init__(self, max_bytes: int, per_min_bytes: int):
        self._max = int(max_bytes)
        self._per_min = int(per_min_bytes)
        self._history: Dict[str, Deque[Tuple[float, int]]] = {}

    def check(self, paths: Iterable, now: float) -> str:
        """'' or the FR-UE-11 flood message for the first file over a limit."""
        for path in paths:
            try:
                size = os.stat(path).st_size
            except OSError:
                continue
            if size > self._max:
                return f"Unreal log grew past {self._max // MB} MB ({size // MB} MB); terminated (log flood)."
            history = self._history.setdefault(str(path), collections.deque())
            history.append((now, size))
            while history and history[0][0] < now - FLOOD_WINDOW_SECONDS:
                history.popleft()
            growth = size - history[0][1]
            if growth > self._per_min:
                return (f"Unreal log grew past {self._per_min // MB} MB/min ({growth // MB} MB); "
                        "terminated (log flood).")
        return ''


class Members:
    """The watched processes of one launch and their ledger."""

    def __init__(self, root_pid: int, job=None, ledger_path=None,
                 process_factory: Callable = psutil.Process):
        self._root_pid = root_pid
        self._job = job
        self._ledger_path = Path(ledger_path) if ledger_path else None
        self._factory = process_factory
        self._cache: Dict[int, object] = {}
        self._ledger: Optional[Dict[Tuple[int, float], dict]] = None
        self._dirty = False

    def _proc(self, pid: int):
        proc = self._cache.get(pid)
        if proc is None or not proc.is_running():
            proc = self._factory(pid)
            self._cache[pid] = proc
        return proc

    def current(self) -> List:
        """Live member processes (Job Object list, or root + descendants)."""
        procs = []
        if self._job is not None:
            pids = self._job.pids()
            for pid in [cached for cached in self._cache if cached not in pids]:
                del self._cache[pid]
            for pid in pids:
                try:
                    procs.append(self._proc(pid))
                except (psutil.Error, OSError):
                    continue
            return procs
        try:
            root = self._proc(self._root_pid)
            return [root] + list(root.children(recursive=True))
        except (psutil.Error, OSError):
            return procs

    def _seed(self) -> Dict[Tuple[int, float], dict]:
        """Entries of an earlier phase's ``procs.json`` in the same job directory."""
        try:
            if self._ledger_path.stat().st_size > MAX_LEDGER_BYTES:
                return {}
            with open(self._ledger_path, 'r', encoding='utf-8') as handle:
                items = json.load(handle).get('procs', [])
            return {(int(e['pid']), float(e['create_time'])): e for e in items if isinstance(e, dict)}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return {}

    def write_ledger(self, procs: Iterable) -> None:
        """Atomically replace ``procs.json`` when a new member appears (FR-UE-23 input).

        Cumulative: a member that already exited stays listed; the sweep
        re-checks ``(create_time, exe)`` and the owner before it kills.
        """
        if self._ledger_path is None:
            return
        if self._ledger is None:
            self._ledger = self._seed()
            self._dirty = True
        for proc in procs:
            try:
                key = (proc.pid, proc.create_time())
                if key not in self._ledger and len(self._ledger) < MAX_LEDGER_ENTRIES:
                    self._ledger[key] = {'pid': proc.pid, 'create_time': key[1], 'exe': proc.exe() or ''}
                    self._dirty = True
            except (psutil.Error, OSError):
                continue
        if self._dirty:
            self._write()

    def _write(self) -> None:
        payload = sorted(self._ledger.values(), key=lambda item: (item['pid'], item['create_time']))
        tmp = self._ledger_path.with_name(f"{self._ledger_path.name}.tmp-{uuid.uuid4().hex[:8]}")
        try:
            with open(tmp, 'w', encoding='utf-8') as handle:
                json.dump({'schema': 1, 'procs': payload}, handle)
            os.replace(tmp, self._ledger_path)
            self._dirty = False
        except OSError as exc:
            logger.warning("Could not write the Unreal process ledger %s (%s); retrying next tick.",
                           self._ledger_path, exc)
            try:
                os.unlink(tmp)
            except OSError:
                pass
