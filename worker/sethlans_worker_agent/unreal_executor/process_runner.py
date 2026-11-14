# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
UE process runner (Unreal foundation FR-UE-11; Conc H1, M4, M10, R2-M1).

``run(spec)`` launches one UE process and supervises it to the end:

* **Launch.** The FR-UE-27 environment is checked first (the runner
  refuses a UE process without the seven isolation variables or with
  another ``USERPROFILE``). ``Popen(list, shell=False, cwd=CACHE_DIR,
  stdout=<file>, stderr=STDOUT)``: no PIPE, so no pipe can deadlock.
  On Windows the process starts ``CREATE_SUSPENDED | CREATE_NO_WINDOW |
  CREATE_NEW_PROCESS_GROUP``, is assigned to a new kill-on-close Job
  Object, and only then resumed. A Job Object failure kills it and raises
  ``UnconfinedLaunchError`` (fail closed, ``job_object=failed``).
* **Monitor** (every ``TICK_SECONDS``; every ``VRAM_TICK_SECONDS`` while
  sampling GPU memory): fatal rules on the tailed lines, the ledger, tree
  CPU, GPU memory samples, log flood, the success marker and quit grace,
  ``tick_check``, the cancel cause, ``shutdown_event``, the wall timeout
  and the hang rule.
* **Exit confirmation** runs in ``finally`` on every path (including an
  exception mid-monitor): terminate the Job Object, wait for
  ``ActiveProcesses == 0`` (30 s), record the survivors. It is idempotent,
  and ``RunnerRegistry.confirm_all`` repeats it for any runner still open
  (``execute()``'s outermost ``finally``, FR-UE-2).

Everything runs in the caller's (job) thread; no thread is started.

**Contract for the executor (chunk 7b).**

* ``run(spec, registry)``: ``registry`` is required; one ``RunnerRegistry``
  per ``execute()``.
* ``registry.confirm_all()`` is called only from the job thread's outermost
  ``finally`` (FR-UE-2), never from another thread.
* The reservation is **never** released (``reservation.release()``) before
  ``confirm_all()`` has returned: ``retain_until_restart()`` is a no-op on a
  reservation that was already released.
* Non-empty survivors (from ``RunOutcome.survivors`` or ``confirm_all``):
  ``reservation.retain_until_restart()``, then
  ``runtime.disable_until_restart(SURVIVORS_DISABLED_REASON)``, then
  ``JobWorkspace.finish(survivors=True)``; the job fails with
  ``SURVIVORS_MESSAGE``.
* ``tick_check``: a **fresh** ``zen_port.LoopbackWatch`` per phase.
* ``vram_sample``: ``phase_vram_sample(sampler)``.
"""
import logging
import subprocess  # nosec B404 - list-form, shell=False
import sys
import threading
import time
from typing import Callable, List, Optional, Tuple

import psutil

from sethlans_worker_agent.unreal_executor import process_tree, user_state
from sethlans_worker_agent.unreal_executor.job_object import JobObject, JobObjectError
from sethlans_worker_agent.unreal_executor.runner_monitor import LogTail, Members
from sethlans_worker_agent.unreal_executor.runner_state import MonitorState
from sethlans_worker_agent.unreal_executor.runner_types import (
    EXIT_WAIT_SECONDS, TICK_SECONDS, UNCONFINED_MESSAGE, VRAM_SAMPLE_TIMEOUT_S, VRAM_TICK_SECONDS, RunOutcome,
    RunSpec, UnconfinedLaunchError, VramTrace)

logger = logging.getLogger(__name__)

CREATE_SUSPENDED = 0x00000004
FINAL_DRAIN_SECONDS = 2.0  # bounded drain of the tail after exit; validation re-reads the whole log anyway
UNKNOWN_SURVIVOR = ((0, 'unknown (exit confirmation failed)'),)


def _is_windows() -> bool:
    return sys.platform == 'win32'


class ProcessRunner:

    def __init__(self, spec: RunSpec, job_api=None, popen: Callable = subprocess.Popen,
                 clock: Callable[[], float] = time.monotonic, windows: Optional[bool] = None,
                 process_factory: Callable = psutil.Process, process_iter: Callable = psutil.process_iter,
                 exit_wait_s: float = EXIT_WAIT_SECONDS):
        self.spec = spec
        self._exit_wait_s = exit_wait_s
        self._process_iter = process_iter
        self._job_api = job_api
        self._popen = popen
        self._clock = clock
        self._windows = _is_windows() if windows is None else windows
        self._factory = process_factory
        self.proc = None
        self.job: Optional[JobObject] = None
        self._snapshot: List = []
        self._confirmed: Optional[Tuple[Tuple[int, str], ...]] = None
        self._lock = threading.Lock()  # leaf lock: confirm_exit may run from execute()'s finally
        self.vram = VramTrace()

    # -- launch ---------------------------------------------------------
    def start(self) -> None:
        spec = self.spec
        user_state.check_isolated_env(spec.launch.env, spec.launch.profile)
        flags = 0
        if self._windows:
            flags = (CREATE_SUSPENDED | getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                     | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))
            try:
                self.job = JobObject.create(self._job_api)
            except JobObjectError as exc:
                raise UnconfinedLaunchError(UNCONFINED_MESSAGE) from exc
        logger.info("Launching Unreal (%s): %s", spec.phase, subprocess.list2cmdline(list(spec.launch.args)))
        with open(spec.stdout_path, 'wb') as stdout:
            kwargs = {'cwd': spec.launch.cwd, 'env': dict(spec.launch.env), 'stdout': stdout,
                      'stderr': subprocess.STDOUT, 'stdin': subprocess.DEVNULL, 'shell': False}
            if self._windows:
                kwargs['creationflags'] = flags
            else:
                kwargs['start_new_session'] = True
            self.proc = self._popen(list(spec.launch.args), **kwargs)
        if self._windows:
            self._confine()

    def _confine(self) -> None:
        try:
            self.job.assign(int(self.proc._handle))
        except (JobObjectError, AttributeError, TypeError, ValueError) as exc:
            logger.error("Job Object assignment failed: %s", exc)
            self._kill_unconfined()
            raise UnconfinedLaunchError(UNCONFINED_MESSAGE) from exc
        try:
            self._factory(self.proc.pid).resume()
        except (psutil.Error, OSError) as exc:
            logger.error("Could not resume the Unreal process: %s", exc)
            raise UnconfinedLaunchError(f"Could not resume the Unreal process ({type(exc).__name__}).") from exc

    def _kill_unconfined(self) -> None:
        try:
            self.proc.kill()
            self.proc.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            logger.exception("Could not kill the unconfined Unreal process.")
        job, self.job = self.job, None
        if job is not None:
            job.close()

    # -- monitor --------------------------------------------------------
    def monitor(self) -> RunOutcome:
        state = MonitorState(self.spec, self._clock())
        tail = LogTail(self.spec.log_path)
        members = Members(self.proc.pid, job=self.job, ledger_path=self.spec.ledger_path,
                          process_factory=self._factory)
        interval = VRAM_TICK_SECONDS if self.spec.vram_sample else TICK_SECONDS
        next_tick = state.started
        try:
            reason = None
            while reason is None:
                now = self._clock()
                exited = self.proc.poll() is not None
                reason = state.lines(tail.read_new(), now)
                if reason is None and exited:
                    reason = 'exited'
                    break
                if reason is None:
                    reason = self._tick(state, members, now)
                if reason is None:
                    next_tick += interval  # fixed schedule: a slow tick does not stretch the cadence (Conc L4)
                    state.waiter.wait(max(0.0, next_tick - self._clock()))
            if reason != 'exited':
                self._terminate()
            if reason not in ('shutdown', 'fatal'):
                self._drain(tail, state)
        finally:
            tail.close()
        return self._outcome(state, reason)

    def _drain(self, tail: LogTail, state: MonitorState) -> None:
        """Read what the tail has not seen yet, for at most ``FINAL_DRAIN_SECONDS`` (Conc L6)."""
        deadline = self._clock() + FINAL_DRAIN_SECONDS
        while self._clock() < deadline:
            lines = tail.read_new()
            if not lines:
                break
            state.lines(lines, self._clock())

    def _tick(self, state: MonitorState, members: Members, now: float) -> Optional[str]:
        procs = members.current()
        if not self._windows:
            self._snapshot = procs or self._snapshot
        members.write_ledger(procs)
        state.cpu.update(procs, now)
        if self.spec.vram_sample is not None:
            self.vram.add(self.spec.vram_sample(VRAM_SAMPLE_TIMEOUT_S))
        return state.decide(now)

    def _terminate(self) -> None:
        if self.job is not None:
            try:
                self.job.terminate()
            except JobObjectError as exc:
                logger.error("TerminateJobObject failed: %s", exc)
        else:
            snapshot = process_tree.snapshot_tree(self.proc.pid, self._factory) or self._snapshot
            self._snapshot = snapshot
            process_tree.kill_tree_fallback(snapshot, self.spec.excluded_images)

    def _outcome(self, state: MonitorState, reason: str) -> RunOutcome:
        baseline = self.spec.vram_baseline
        peak = self.vram.peak_over(baseline) if baseline is not None else None
        return RunOutcome(
            exit_code=self.proc.poll(), reason=reason, rule_counts=dict(state.scan.counts),
            wall_s=self._clock() - state.started, survivors=(), vram_peak_mb=peak,
            phase=self.spec.phase, success_marker_seen=state.marker_at is not None,
            quit_forced=reason == 'exited_after_grace', fatal_rule=state.fatal_rule, detail=state.detail,
            job_object='ok' if self.job is not None else 'none', vram=self.vram)

    # -- exit confirmation ---------------------------------------------
    def _confirm(self) -> Tuple[Tuple[int, str], ...]:
        if self.proc is None:
            return ()
        if self.job is not None:
            return process_tree.confirm_job_exit(self.job, timeout_s=self._exit_wait_s, process_factory=self._factory)
        if self._windows:  # never confined (the launch was refused): the process was killed
            return () if self.proc.poll() is not None else ((int(self.proc.pid), 'unconfined process'),)
        snapshot = process_tree.snapshot_tree(self.proc.pid, self._factory) or self._snapshot
        return process_tree.confirm_fallback_exit(snapshot, self.spec.excluded_images, timeout_s=self._exit_wait_s,
                                                  process_iter=self._process_iter)

    def confirm_exit(self) -> Tuple[Tuple[int, str], ...]:
        """Terminate and confirm exit; idempotent; returns the survivors."""
        with self._lock:
            if self._confirmed is not None:
                return self._confirmed
            survivors: Tuple[Tuple[int, str], ...] = UNKNOWN_SURVIVOR  # fail closed if _confirm raises
            try:
                survivors = self._confirm()
            finally:
                job, self.job = self.job, None
                if job is not None:
                    job.close()
                self._confirmed = survivors
            if survivors:
                logger.error("Unreal processes did not exit: %s", survivors)
            return survivors


def run(spec: RunSpec, registry: 'RunnerRegistry', **runner_kwargs) -> RunOutcome:
    """Launch, supervise and confirm the exit of one UE process (FR-UE-11)."""
    if registry is None:
        raise ValueError("process_runner.run needs the execute()'s RunnerRegistry")
    runner = ProcessRunner(spec, **runner_kwargs)
    registry.add(runner)
    try:
        runner.start()
        outcome = runner.monitor()
    finally:
        survivors = runner.confirm_exit()
    outcome.survivors = survivors
    return outcome


class RunnerRegistry:
    """Every runner of one ``execute()``; ``confirm_all`` is its outermost ``finally``."""

    def __init__(self):
        self._lock = threading.Lock()  # leaf lock
        self._runners: List[ProcessRunner] = []

    def add(self, runner: ProcessRunner) -> None:
        with self._lock:
            self._runners.append(runner)

    def confirm_all(self) -> Tuple[Tuple[int, str], ...]:
        with self._lock:
            runners = list(self._runners)
        survivors = []
        for runner in runners:
            try:
                survivors.extend(runner.confirm_exit())
            except Exception:  # noqa: BLE001 - keep confirming the others; fail closed
                logger.exception("Exit confirmation failed.")
                survivors.extend(UNKNOWN_SURVIVOR)
        return tuple(survivors)


def phase_vram_sample(sampler) -> Callable:
    """``RunSpec.vram_sample`` for a ``VramSampler``: a reading at most one tick old (FR-WD-10, Dom R3-11).

    The runner calls it every ``VRAM_TICK_SECONDS`` with a relative timeout
    (``VRAM_SAMPLE_TIMEOUT_S``); the deadline is computed here on the
    sampler's own clock (``time.monotonic``), never from the runner's clock
    (Conc L4). Samples are at most 2 s apart and one slow ``nvidia-smi``
    call cannot stretch the interval.
    """
    return lambda timeout_s: sampler.fresh(max_age_s=VRAM_TICK_SECONDS, deadline=time.monotonic() + timeout_s)
