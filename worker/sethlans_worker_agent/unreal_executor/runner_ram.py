# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
RAM figures of one UE launch, measured by the runner that owns its Job Object (render per card
FR-PC-RAM-2, D8).

``RunnerRam`` lives on the runner's own (job) thread; the agent loop never touches a Job Object
or a process handle. Per launch:

* ``launch_started()`` before the process starts and ``launch_ended()`` after the exit is
  confirmed (``process_runner.run`` calls them in ``try``/``finally``);
* ``tick(job, pids)`` on every monitor tick: the private working set and commit
  (``process_memory.private_ws_and_commit``) of every Job Object member, summed; the launch keeps
  the peak summed working set and publishes the sums to ``ram_ledger`` under the claim's id;
* ``read_peak(job)`` after the tree exited and before the Job Object closes:
  ``PeakJobMemoryUsed`` (the launch's commit figure) and ``PeakProcessMemoryUsed``.

**Unknown, never guessed (NF-2).** A member whose read fails but that is no longer in the Job
Object (it exited between the listing and the read) counts nothing; any other failed read makes the
launch's working-set figure unknown and marks the ledger entry ``failed`` (a later good publish
clears the entry, not the figure). A failed peak read makes the commit figures unknown and marks
the entry ``failed`` after ``launch_ended`` (the next launch, or the release, clears it). Without a
Job Object (off Windows) nothing is measured. A figure below 1 MB is not a measurement (no member
was read) and is unknown too.

``read_peak`` runs under the runner's leaf lock, so it touches the Job Object only, never the
ledger. Sizes are MB (bytes // 1,048,576).
"""
import logging
from dataclasses import dataclass
from typing import Callable, Iterable, Optional, Tuple, Union

from sethlans_worker_agent.unreal_executor import process_memory
from sethlans_worker_agent.unreal_executor.job_object import JobObjectError

logger = logging.getLogger(__name__)

MB = 1024 * 1024


@dataclass(frozen=True)
class RamTarget:
    """Where a launch publishes: the RAM ledger and the claim's id (D16).

    ``full_need`` and ``render_need`` (``(ws_mb, commit_mb)``, ``None``: not changed) are the claim's
    registered need and its render phase's figure: a warm-up launch raises the need to ``full_need``
    as it starts and lowers it to ``render_need`` once its process tree has exited (FR-PC-RAM-4).
    """
    ledger: object      # ram_ledger.RamLedger
    claim_id: str
    full_need: Optional[Tuple[int, int]] = None
    render_need: Optional[Tuple[int, int]] = None


@dataclass(frozen=True)
class PhaseRam:
    """One launch's figures; ``None`` = unknown."""
    ws_mb: Optional[int] = None               # peak summed private working set of the members
    commit_mb: Optional[int] = None           # PeakJobMemoryUsed
    process_commit_mb: Optional[int] = None   # PeakProcessMemoryUsed (the largest single member)


def _mb(size: int) -> Optional[int]:
    value = int(size) // MB
    return value if value >= 1 else None


class RunnerRam:
    """The RAM side of one ``ProcessRunner``; not thread-safe (the job thread only)."""

    def __init__(self, target: Optional[RamTarget] = None,
                 reader: Optional[Callable[[int], Tuple[int, int]]] = None, phase: str = ''):
        self._target = target
        self._warmup = phase == 'warmup'
        self._read = reader or process_memory.private_ws_and_commit
        self._peak_ws = 0
        self._measured = False
        self._ws_failed = False
        self._peaks: Optional[Tuple[int, int]] = None
        self._peak_failed = False

    def _set_need(self, need: Optional[Tuple[int, int]]) -> None:
        if self._warmup and self._target is not None and need is not None:
            self._target.ledger.set_need(self._target.claim_id, *need)

    def launch_started(self) -> None:
        if self._target is not None:
            self._set_need(self._target.full_need)  # a retry warm-up needs the warm-up figure again
            self._target.ledger.launch_started(self._target.claim_id)

    def launch_ended(self, tree_exited: bool = False) -> None:
        """``tree_exited``: every process of the launch's tree is gone (no survivors)."""
        if self._target is not None:
            self._target.ledger.launch_ended(self._target.claim_id)
            if self._peak_failed:
                self._target.ledger.mark_failed(self._target.claim_id)
            if tree_exited:
                self._set_need(self._target.render_need)  # the warm-up is over: the render's figure

    def _mark_failed(self) -> None:
        if self._target is not None:
            self._target.ledger.mark_failed(self._target.claim_id)

    def tick(self, job, pids: Iterable[int]) -> None:
        """Sum the members' figures, keep the peak working set, publish (FR-PC-RAM-2)."""
        if job is None:
            return
        totals = self._sum(job, pids)
        if isinstance(totals, str):
            if not self._ws_failed:  # once per launch; the ledger entry is marked on every failed tick
                logger.warning("%s; the Unreal launch's working-set peak is unknown.", totals)
            self._ws_failed = True
            self._mark_failed()
            return
        ws, commit, counted = totals
        if counted:
            self._measured = True
            self._peak_ws = max(self._peak_ws, ws)
        if self._target is not None:
            self._target.ledger.publish(self._target.claim_id, ws // MB, commit // MB)

    def _sum(self, job, pids: Iterable[int]) -> Union[Tuple[int, int, int], str]:
        """``(ws, commit, members read)`` in bytes, or what failed."""
        ws = commit = counted = 0
        for pid in pids:
            try:
                private_ws, private_commit = self._read(pid)
            except process_memory.ProcessMemoryError as exc:
                if self._still_member(job, pid):
                    return f"RAM of Unreal process {pid} could not be read ({exc})"
                continue  # it exited between the listing and the read: it holds nothing now
            ws += private_ws
            commit += private_commit
            counted += 1
        return ws, commit, counted

    @staticmethod
    def _still_member(job, pid: int) -> bool:
        try:
            return pid in job.pids()
        except JobObjectError as exc:
            logger.warning("Could not list the Job Object's members: %s", exc)
            return True  # cannot tell: the read counts as failed (fail closed)

    def read_peak(self, job) -> None:
        """``(PeakJobMemoryUsed, PeakProcessMemoryUsed)`` of the launch; a failure makes them unknown."""
        try:
            self._peaks = job.peak_memory()
        except JobObjectError as exc:
            logger.error("The Unreal launch's memory peak could not be read: %s", exc)
            self._peak_failed = True

    def result(self) -> PhaseRam:
        ws = _mb(self._peak_ws) if self._measured and not self._ws_failed else None
        commit = process = None
        if self._peaks is not None:
            commit, process = _mb(self._peaks[0]), _mb(self._peaks[1])
        return PhaseRam(ws_mb=ws, commit_mb=commit, process_commit_mb=process)
