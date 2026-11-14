# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Termination and exit confirmation of a UE process tree (Unreal foundation
FR-UE-11 "Termination", "Exit confirmation").

* Windows: ``TerminateJobObject`` kills the whole job at once (on Windows
  ``psutil.terminate()`` is already ``TerminateProcess``; there is no
  graceful phase). ``confirm_job_exit`` terminates, waits up to
  ``EXIT_WAIT_SECONDS`` for ``ActiveProcesses == 0`` and returns the
  survivors ``[(pid, exe)]``.
* Fallback (non-Windows): snapshot the tree, kill the **root first** (so
  UE cannot relaunch crashed ShaderCompileWorkers), then the descendants;
  a zenserver is spared **by exact image path**, never by a name prefix.
  ``confirm_fallback_exit`` waits with ``psutil.wait_procs`` and
  re-enumerates once.

This module does not import ``blender_yield.terminate_process_tree``
(implementation boundary, FR-UE-11).
"""
import logging
import time
from typing import Callable, Iterable, List, Tuple

import psutil

from sethlans_worker_agent.unreal_executor import winpath
from sethlans_worker_agent.unreal_executor.job_object import JobObjectError
from sethlans_worker_agent.unreal_executor.runner_types import EXIT_WAIT_SECONDS

logger = logging.getLogger(__name__)

POLL_SECONDS = 0.1
Survivor = Tuple[int, str]


def describe(procs: Iterable) -> Tuple[Survivor, ...]:
    out = []
    for proc in procs:
        try:
            exe = proc.exe() or proc.name()
        except (psutil.Error, OSError):
            exe = '?'
        out.append((int(proc.pid), exe))
    return tuple(sorted(out))


def _excluded(proc, excluded_images: frozenset) -> bool:
    try:
        return winpath.normcase(proc.exe() or '') in {winpath.normcase(path) for path in excluded_images}
    except (psutil.Error, OSError):
        return False


def snapshot_tree(root_pid: int, process_factory: Callable = psutil.Process) -> List:
    """Root first, then every descendant (empty when the root is gone)."""
    try:
        root = process_factory(root_pid)
        return [root] + list(root.children(recursive=True))
    except (psutil.Error, OSError):
        return []


def kill_tree_fallback(snapshot: List, excluded_images: frozenset) -> None:
    """Kill the root first, then the descendants; zenserver by exact image path is spared."""
    for proc in snapshot:
        if _excluded(proc, excluded_images):
            continue
        try:
            proc.kill()
        except (psutil.Error, OSError):
            continue


def confirm_fallback_exit(snapshot: List, excluded_images: frozenset, timeout_s: float = EXIT_WAIT_SECONDS,
                          process_iter: Callable = psutil.process_iter) -> Tuple[Survivor, ...]:
    """Kill, wait for the snapshot, re-enumerate once; return the survivors."""
    targets = [proc for proc in snapshot if not _excluded(proc, excluded_images)]
    kill_tree_fallback(targets, excluded_images)
    _gone, alive = psutil.wait_procs(targets, timeout=timeout_s)
    known = {proc.pid for proc in snapshot}
    late = []
    for proc in process_iter():
        try:
            if proc.ppid() in known and proc.pid not in known and not _excluded(proc, excluded_images):
                late.append(proc)
        except (psutil.Error, OSError):
            continue
    kill_tree_fallback(late, excluded_images)
    _gone, late_alive = psutil.wait_procs(late, timeout=min(timeout_s, 5.0)) if late else ([], [])
    return describe(list(alive) + list(late_alive))


def _terminate(job, log: bool = True) -> bool:
    """``TerminateJobObject``; False when it failed (logged only when ``log``)."""
    try:
        job.terminate()
    except JobObjectError as exc:
        if log:
            logger.error("TerminateJobObject failed: %s", exc)
        return False
    return True


def _wait_empty(job, timeout_s: float, clock: Callable[[], float], sleep: Callable[[float], None]) -> bool:
    """True once the job has no active process; False on timeout or a query failure.

    The job is terminated again on every poll, so a member that was being
    created while the first termination ran cannot outlive the wait.
    """
    deadline = clock() + timeout_s
    logged = False
    while True:
        if not _terminate(job, log=not logged):
            logged = True  # a persistent failure is logged once per wait, not every poll
        try:
            if job.active_processes() == 0:
                return True
        except JobObjectError as exc:
            logger.error("Could not read the Job Object's process count: %s", exc)
            return False
        if clock() >= deadline:
            return False
        sleep(POLL_SECONDS)


def _job_survivors(job, process_factory: Callable) -> Tuple[Survivor, ...]:
    try:
        pids = job.pids()
    except JobObjectError:
        return ((0, 'unknown (Job Object query failed)'),)
    procs = []
    for pid in pids:
        try:
            procs.append(process_factory(pid))
        except (psutil.Error, OSError):
            continue
    return describe(procs) or ((0, 'unknown (Job Object still active)'),)


def confirm_job_exit(job, timeout_s: float = EXIT_WAIT_SECONDS, clock: Callable[[], float] = time.monotonic,
                     sleep: Callable[[float], None] = time.sleep,
                     process_factory: Callable = psutil.Process) -> Tuple[Survivor, ...]:
    """Terminate the job, wait for ``ActiveProcesses == 0``; the survivors (fail closed)."""
    if _wait_empty(job, timeout_s, clock, sleep):
        return ()
    return _job_survivors(job, process_factory)
