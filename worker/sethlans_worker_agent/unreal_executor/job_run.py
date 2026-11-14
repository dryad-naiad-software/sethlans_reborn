# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``UnrealExecutor.execute`` for one claim (Unreal foundation FR-UE-2,
FR-UE-11, FR-UE-17, FR-UE-24; Conc R2-M1; chunk 7 integration
requirements 2-6, 8, 9).

Order: cancel watcher -> ``j/<id>-<att8>/`` -> ``prepare_workspace``
(cache, overlay, working copy) -> ``zen_guard.scan_project_config`` ->
``UeFlow`` (prep, warm-up, render, retry; each launch through the
pre-launch check) -> **outermost** ``finally``: ``registry.confirm_all()``
(no UE process outlives ``execute``), survivors handling, then
``JobWorkspace.finish(survivors=..., abort=NEVER)`` -> delivery (upload
from the checked handle, or ``pilot-result/``, or release).

Survivors (from a phase or ``confirm_all``): ``reservation.retain_until_
restart()`` -> ``runtime.disable_until_restart(SURVIVORS_DISABLED_REASON)``
-> ``finish(survivors=True)`` (the entry is quarantined); the job fails
with ``SURVIVORS_MESSAGE``. The reservation is never released here; the
lifecycle releases it after ``execute`` returns (unless retained).

A shutdown skips ``finish`` (the re-hash would not fit the shutdown
budget): the entry is **quarantined** instead (dropped from ``index.json``,
nothing deleted now), so a restart never trusts an entry UE may have
touched; FR-PA-18 removes it and the working copy on the next start.

Any unexpected exception from the phases becomes an ``ERROR`` outcome, so
exit confirmation, ``finish``, delivery and the post-report cleanup always
run (``BaseException`` still propagates after the outermost ``finally``).
"""
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Callable

from sethlans_worker_agent.project_archive.abort import NEVER, Abort
from sethlans_worker_agent.project_archive.errors import (
    AbortedError, ArchiveError, CacheUnavailableError, ShareUnavailableError)
from sethlans_worker_agent.project_archive.workspace import ArchiveRequest
from sethlans_worker_agent.unreal_executor import job_dir, user_state, warmup
from sethlans_worker_agent.unreal_executor.cancel_watch import CancelCause, CancelWatcher
from sethlans_worker_agent.unreal_executor.command import CommandError
from sethlans_worker_agent.unreal_executor.delivery import Delivery, Outcome
from sethlans_worker_agent.unreal_executor.job_flow import UeFlow
from sethlans_worker_agent.unreal_executor.outcomes import Cancelled, JobFailure, LocalConflict, SurvivorsLeft
from sethlans_worker_agent.unreal_executor.runner_types import (
    SURVIVORS_DISABLED_REASON, SURVIVORS_MESSAGE, UnconfinedLaunchError)
from sethlans_worker_agent.unreal_executor.summary import Summary

logger = logging.getLogger(__name__)


@dataclass
class RunDeps:
    """Everything ``JobRun`` touches outside the job directory (``executor.default_run_deps``)."""
    cache: Callable
    limits: Callable
    reachability: Callable
    prepare_workspace: Callable
    zen_scan: Callable
    engine_cvars: Callable
    sampler: Callable
    registry_factory: Callable
    phase_runner: Callable          # (config, registry, job, cancel, shutdown, sampler) -> PhaseRunner
    phase_config: Callable          # (cache_dir, merged_names, install) -> PhaseConfig
    launch_context: Callable        # (install, cache_dir) -> command.LaunchContext
    builders: object
    flow_config: Callable           # (profile) -> FlowConfig
    get_assignment: Callable
    worker_id: Callable
    delivery: object                # DeliveryDeps
    disable_until_restart: Callable[[str], None]
    keep_job_logs: Callable[[], int]
    prep_timeout_s: Callable[[], float]
    cancel_poll_s: Callable[[], float]


class JobRun:

    def __init__(self, reservation, ctx, deps: RunDeps):
        self.reservation, self.ctx, self.deps = reservation, ctx, deps
        self.params, self.install = reservation.params, reservation.install
        self.summary = Summary()
        self.cancel = CancelCause()
        self.registry = deps.registry_factory()
        self.job = self.ws = self.layout = None

    def cause(self) -> str:
        if self.ctx.shutdown_event is not None and self.ctx.shutdown_event.is_set():
            return 'shutdown'
        return self.cancel.cause or ''

    def _start_summary(self) -> None:
        p = self.params
        self.summary.update({'engine': p.engine_build, 'kind': p.kind, 'exclusive': 1,
                             'user_state': user_state.SUMMARY.split('=', 1)[1], 'quit_forced': '',
                             'cvar_overridden': '', 'job_object': 'skipped'})
        self.summary.update(self.reservation.gate.summary)

    # -- body -----------------------------------------------------------
    def _workspace(self, cache, att8: str):
        p = self.params
        request = ArchiveRequest(job_id=p.job_id, attempt=att8, share_root=p.share_root, archive_relpath=p.archive_path,
                                 archive_sha256=p.archive_sha256, uproject_relpath=p.uproject_path,
                                 overlay_relpath=p.overlay_path, overlay_sha256=p.overlay_sha256)
        abort = Abort(cancel=self.cancel, shutdown=self.ctx.shutdown_event,
                      deadline=time.monotonic() + self.deps.prep_timeout_s())
        self.ws = self.deps.prepare_workspace(cache, request, limits=self.deps.limits(),
                                              reachability=self.deps.reachability(), abort=abort)
        self.summary.update(self.ws.summary)

    def _guard_and_counts(self) -> None:
        guard = self.deps.zen_scan(self.ws.working_copy.project_dir)
        self.summary.set('zen_ddc_guard', guard.summary.split('=', 1)[1])
        if not guard.ok:
            raise JobFailure(guard.message)
        names = [name for name, _value in self.params.merged]
        engine = self.deps.engine_cvars(self.install.root, names)
        self.summary.set('engine_cvars', engine.count)
        for name in engine.collisions:
            self.summary.append('engine_cvar_collision', name)
        if engine.warn:
            self.summary.warn(f"engine ConsoleVariables.ini has {engine.count} startup entries")

    def _body(self):
        cache = self.deps.cache()
        if cache is None:
            raise CacheUnavailableError("The Unreal cache is not ready (startup recovery did not succeed).")
        self.layout = cache.layout
        att8 = uuid.uuid4().hex[:8]
        self.summary.set('attempt', att8)
        self.job = job_dir.create(cache.layout, self.params.job_id, att8)
        self._workspace(cache, att8)
        self._guard_and_counts()
        cache_dir = str(cache.layout.root)
        names = [name for name, _value in self.params.merged]
        runner = self.deps.phase_runner(self.deps.phase_config(cache_dir, names, self.install), self.registry,
                                        self.job, self.cause, self.ctx.shutdown_event, self.deps.sampler())
        self.runner = runner
        runner.project_dir, runner.zen_scan = self.ws.working_copy.project_dir, self.deps.zen_scan  # every phase
        marker = warmup.marker_path(self.ws.entry.entry_dir, self.params.overlay_sha256, self.params.engine_build)
        profile = user_state.profile_dir(cache_dir)
        flow = UeFlow(self.params, self.deps.launch_context(self.install, cache_dir), self.job, runner, self.summary,
                      self.deps.builders, marker, self.ws.working_copy.uproject, self.deps.flow_config(profile),
                      reading=self.reservation.gate.reading)
        try:
            return flow.run()
        finally:
            self._runner_summary(runner)

    def _runner_summary(self, runner) -> None:
        self.summary.update({'user_profile_reset': runner.profile_reset, 'user_cvars': runner.user_cvars,
                             'zen': f"private:{runner.config.zen_port}" if runner.zen_ok else 'FAILED'})
        if runner.runs:  # 'skipped' (the default) when no UE process was launched (review L4)
            ok = all(r.outcome.job_object == 'ok' for r in runner.runs)
            self.summary.set('job_object', 'ok' if ok else 'failed')
        for name in runner.collisions:
            self.summary.append('user_cvar_collision', name)

    def _attempt(self) -> Outcome:
        try:
            return Outcome('success', flow=self._body())
        except (JobFailure, LocalConflict, Cancelled, AbortedError, ShareUnavailableError, ArchiveError,
                SurvivorsLeft, UnconfinedLaunchError, CommandError, user_state.IsolationError) as exc:
            return self._map(exc)
        except Exception as exc:  # noqa: BLE001 - a bug still gets delivery, cleanup and an ERROR report
            logger.exception("Unreal job %s failed unexpectedly.", self.params.job_id)
            return Outcome('error', f"Unreal job failed unexpectedly ({type(exc).__name__}).")

    def _map(self, exc: Exception) -> Outcome:
        """The ``Outcome`` of an expected failure (anything else propagates after the cleanup)."""
        if isinstance(exc, JobFailure):
            return Outcome('error', exc.message)
        if isinstance(exc, LocalConflict):
            return Outcome('release', exc.message, exc.reason)
        if isinstance(exc, (Cancelled, AbortedError)):
            return Outcome('cancel', reason=self.cause() or getattr(exc, 'cause', 'canceled'))
        if isinstance(exc, ShareUnavailableError):
            return Outcome('share', str(exc) or 'share unavailable')
        if isinstance(exc, CacheUnavailableError):
            self.deps.disable_until_restart(str(exc))
        if isinstance(exc, SurvivorsLeft):
            return Outcome('survivors', SURVIVORS_MESSAGE)
        if isinstance(exc, UnconfinedLaunchError):
            self.summary.set('job_object', 'failed')
        if isinstance(exc, (CommandError, user_state.IsolationError)):
            return Outcome('error', f"Unreal launch refused: {exc}")
        return Outcome('error', str(exc))

    # -- outermost ------------------------------------------------------
    def _confirm_and_finish(self, outcome: Outcome) -> tuple:
        survivors = self.registry.confirm_all()
        if survivors or outcome.kind == 'survivors':
            self.reservation.retain_until_restart()
            self.deps.disable_until_restart(SURVIVORS_DISABLED_REASON)
            outcome = Outcome('survivors', SURVIVORS_MESSAGE)
            self.summary.set('survivors', ';'.join(f"{pid}:{exe}" for pid, exe in survivors) or 'phase')
        else:
            self.summary.set('survivors', '')
        integrity_ok = True
        if self.ws is not None and outcome.kind == 'cancel' and outcome.reason == 'shutdown':
            self._quarantine_for_shutdown()
        elif self.ws is not None:
            try:
                report = self.ws.finish(survivors=outcome.kind == 'survivors', abort=NEVER)
                integrity_ok = report.own_ok
            except Exception:  # noqa: BLE001 - an unverifiable entry is a failed one
                logger.exception("Workspace finish failed for job %s", self.params.job_id)
                integrity_ok = False
            self.summary.set('pristine_integrity', self.ws.summary.get('pristine_integrity', 'FAILED'))
        return outcome, integrity_ok

    def _quarantine_for_shutdown(self) -> None:
        """Shutdown skips the re-hash (too slow for the budget), so the entry is never trusted again (review L1).

        ``quarantine`` drops it from ``index.json`` (one small fsynced write) while this job still holds its
        refcount, so nothing is deleted now; recovery discards the unindexed ``p/`` directory on the next start.
        """
        try:
            self.ws.cache.quarantine(self.ws.entry.sha256, 'shutdown before the post-job integrity check')
        except Exception:  # noqa: BLE001 - shutdown must proceed
            logger.exception("Could not quarantine the cache entry of job %s on shutdown", self.params.job_id)
        self.summary.set('pristine_integrity', 'skipped(shutdown; entry quarantined)')

    def _after_report(self):
        layout, job, keep = self.layout, self.job, self.deps.keep_job_logs()
        if layout is None or job is None:
            return None
        return lambda: job_dir.cleanup_after_report(layout, job, keep)

    def run(self):
        self._start_summary()
        watcher = CancelWatcher(self.params.job_id, self.cancel, get_assignment=self.deps.get_assignment,
                                worker_id=self.deps.worker_id(), shutdown_event=self.ctx.shutdown_event,
                                poll_seconds=self.deps.cancel_poll_s())
        outcome = Outcome('error', 'Unreal job did not run.')
        try:
            watcher.start()
            try:
                outcome = self._attempt()
            finally:
                outcome, integrity_ok = self._confirm_and_finish(outcome)
            delivery = Delivery(self.params, self.deps.delivery, self.summary, self._after_report())
            return delivery.deliver(outcome, integrity_ok)
        finally:
            watcher.stop()
