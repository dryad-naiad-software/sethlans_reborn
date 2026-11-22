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
pre-launch check; a ``shots`` job runs ``ShotsFlow``, the prep alone,
FR-SD-4; a sequence piece ``RangeFlow``, FR-SW-3; an audio job ``AudioFlow``, FR-SA) -> **outermost**
``finally``: ``registry.confirm_all()`` (no UE process outlives
``execute``), survivors handling, then ``JobWorkspace.finish(survivors=...,
abort=NEVER)`` -> delivery (upload from the checked handle, or
``pilot-result/``, ``shots-result/``, ``frames/`` and ``piece-result/``,
``sound-result/``, or release), preceded by a render or audio job's
``memory-report/`` (``memory_report``, per-machine memory test FR-MT-S-4).

Survivors (from a phase or ``confirm_all``): ``reservation.retain_until_
restart()`` -> ``runtime.disable_until_restart(SURVIVORS_DISABLED_REASON)``
-> ``finish(survivors=True)`` (the entry is quarantined); the job fails
with ``SURVIVORS_MESSAGE``. The reservation is never released here; the
lifecycle releases it after ``execute`` returns (unless retained).

A shutdown skips ``finish`` (the re-hash would not fit the shutdown
budget): the entry is **quarantined** instead (dropped from ``index.json``,
nothing deleted now), so a restart never trusts an entry UE may have
touched; FR-PA-18 removes it and the working copy on the next start.
The job's own card stop (``ctx.shutdown_event`` is then a ``JobStop``;
cause ``worker_hardware_changed``, C-24 m) ends the phases as a shutdown
does, but ``finish`` runs as usual and the job is released (``delivery``).

The reservation's slot (``reservation.slot``, render per card FR-PC-SLOT)
decides the work copy (``ArchiveRequest.slot``), the farm profile, the Zen
folders and port (``slot.slot_for``) and the warm-up marker; the summary
names it (``slot=``) and the gated card (``card=``). The runner publishes its RAM
figures under the claim (``reservation.ram_target()``, FR-PC-RAM-2); the summary
carries them per phase and the job's own card memory (``job_memory``, FR-PC-RAM-3,
FR-PC-ATT-2), ``''`` until a launch measured them (``job_run_summary``). A picture render's first
output file ends the claim's start-up (``reservation.startup_ended``, FR-PC-SU-1); an audio or
shots job stays starting until its release. The job folder is attached to
the claim's disk ledger entry as soon as it exists, before any launch, and a
piece's disk check goes through it (``reservation.disk_target()``, FR-PC-DSK-1).

Any unexpected exception from the phases becomes an ``ERROR`` outcome, so
exit confirmation, ``finish``, delivery and the post-report cleanup always
run (``BaseException`` still propagates after the outermost ``finally``).
"""
import functools
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Optional

from sethlans_worker_agent import config_unreal
from sethlans_worker_agent.blender_job_stop import WORKER_HARDWARE_CHANGED, JobStop
from sethlans_worker_agent.project_archive.abort import NEVER, Abort
from sethlans_worker_agent.project_archive.errors import CacheUnavailableError
from sethlans_worker_agent.project_archive.workspace import ArchiveRequest
from sethlans_worker_agent.unreal_executor import adapter_choice, adapter_log, job_dir, job_outcome, job_run_summary
from sethlans_worker_agent.unreal_executor import memory_report, spill, warmup
from sethlans_worker_agent.unreal_executor import slot as slots
from sethlans_worker_agent.unreal_executor.audio_flow import AudioFlow
from sethlans_worker_agent.unreal_executor.cancel_watch import CancelCause, CancelWatcher
from sethlans_worker_agent.unreal_executor.delivery import Delivery, Outcome
from sethlans_worker_agent.unreal_executor.job_flow import UeFlow
from sethlans_worker_agent.unreal_executor.outcomes import JobFailure, LocalConflict
from sethlans_worker_agent.unreal_executor.runner_types import SURVIVORS_DISABLED_REASON, SURVIVORS_MESSAGE
from sethlans_worker_agent.unreal_executor.sequence_flow import RangeFlow, ShotsFlow
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
    phase_config: Callable          # (slot, merged_names, install) -> PhaseConfig; slot: slot.Slot
    launch_context: Callable        # (install, slot) -> command.LaunchContext
    builders: object
    flow_config: Callable           # (profile) -> FlowConfig
    get_assignment: Callable
    worker_id: Callable
    delivery: object                # DeliveryDeps
    disable_until_restart: Callable[[str], None]
    keep_job_logs: Callable[[], int]
    prep_timeout_s: Callable[[], float]
    cancel_poll_s: Callable[[], float]
    choose_adapter: Optional[Callable] = None  # (job_id, key=, claim_id=) -> adapter_choice.Choice; None: choose


class JobRun:

    def __init__(self, reservation, ctx, deps: RunDeps):
        self.reservation, self.ctx, self.deps = reservation, ctx, deps
        self.params, self.install = reservation.params, reservation.install
        self.summary = Summary()
        self.cancel = CancelCause()
        self.registry = deps.registry_factory()
        self.job = self.ws = self.layout = self.runner = self.flow = None

    def cause(self) -> str:
        """``shutdown``, then the manager's cause, then ``worker_hardware_changed`` (the job's own card stop, C-24 m)."""
        stop = self.ctx.shutdown_event
        worker = stop.shutdown_event if isinstance(stop, JobStop) else stop
        if worker is not None and worker.is_set():
            return 'shutdown'
        if self.cancel.cause:
            return self.cancel.cause
        return WORKER_HARDWARE_CHANGED if stop is not None and stop.is_set() else ''

    # -- body -----------------------------------------------------------
    def _workspace(self, cache, att8: str):
        p = self.params
        request = ArchiveRequest(job_id=p.job_id, attempt=att8, share_root=p.share_root, archive_relpath=p.archive_path,
                                 archive_sha256=p.archive_sha256, uproject_relpath=p.uproject_path,
                                 overlay_relpath=p.overlay_path, overlay_sha256=p.overlay_sha256,
                                 slot=self.reservation.slot)  # FR-PC-SLOT-6: w/<key16><ov8>-s<n>
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
        if adapter_log.refused(adapter_log.project_key(self.params)):  # before any launch (the reserver declines it too)
            raise LocalConflict('local_conflict', "This project reported another graphics card on this worker; "
                                                  "it is refused here until the worker restarts.")
        att8 = uuid.uuid4().hex[:8]
        self.summary.set('attempt', att8)
        self.job = job_dir.create(cache.layout, self.params.job_id, att8)
        disk = self.reservation.disk_target()
        if disk is not None:
            disk.attach(self.job.root)  # D12: from now on its folder is never a leftover
        self._workspace(cache, att8)
        self._guard_and_counts()
        slot = slots.slot_for(cache.layout.root, config_unreal.ZEN_PORT, self.reservation.slot)
        names = [name for name, _value in self.params.merged]
        runner = self.deps.phase_runner(self.deps.phase_config(slot, names, self.install), self.registry,
                                        self.job, self.cause, self.ctx.shutdown_event, self.deps.sampler())
        self.runner = runner
        runner.project_dir, runner.zen_scan = self.ws.working_copy.project_dir, self.deps.zen_scan  # every phase
        runner.project = adapter_log.project_key(self.params)  # what a mismatch refuses (adapter_log)
        gate = self.reservation.gate
        runner.card_key = gate.card_key  # the gated card: the traces of a launch without a choice (D22)
        runner.ram_target = self.reservation.ram_target()  # each launch publishes its RAM figures (FR-PC-RAM-2)
        p = self.params  # FR-PC-SU-1: a picture render's first output file ends the start-up; audio, shots at the end
        runner.startup_done = None if p.is_audio or p.is_shots else self.reservation.startup_ended
        runner.spill = None if p.is_shots else spill.watch_for(gate, p.job_id)  # D9 on every render launch
        marker = warmup.marker_path(self.ws.entry.entry_dir, self.params.overlay_sha256, self.params.engine_build,
                                    slot.n)
        # FR-SD-4: shots run the prep only; a sequence piece renders a frame range (FR-SW-3); FR-SA: sound only.
        flow_class = ShotsFlow if p.is_shots else RangeFlow if p.is_piece else AudioFlow if p.is_audio else UeFlow
        choose = functools.partial(self.deps.choose_adapter or adapter_choice.choose,  # kept per claim (D16)
                                   claim_id=self.reservation.claim_id)
        flow = flow_class(self.params, self.deps.launch_context(self.install, slot), self.job, runner,
                          self.summary, self.deps.builders, marker, self.ws.working_copy.uproject,
                          self.deps.flow_config(slot.profile), reading=gate.reading, choose=choose,
                          first_key=self.reservation.card_key)  # the held card (render per card D1, D14)
        flow.disk = disk  # a piece's disk check counts the other running claims (FR-PC-DSK-1)
        flow.result_peak_mb = gate.result.peak_mb if getattr(gate, 'result', None) else None  # FR-MT-G-4
        self.flow = flow  # the memory report reads the render launch's driver through it (FR-MT-S-4)
        try:
            return flow.run()
        finally:
            job_run_summary.from_runner(self.summary, runner)

    def _attempt(self) -> Outcome:
        try:
            return Outcome('success', flow=self._body())
        except job_outcome.EXPECTED as exc:
            return job_outcome.outcome_for(exc, self.cause, self.summary, self.deps.disable_until_restart)
        except Exception as exc:  # noqa: BLE001 - a bug still gets delivery, cleanup and an ERROR report
            logger.exception("Unreal job %s failed unexpectedly.", self.params.job_id)
            return Outcome('error', f"Unreal job failed unexpectedly ({type(exc).__name__}).")

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
        # A shutdown after a card stop also skips the re-hash (the shutdown budget).
        if self.ws is not None and outcome.kind == 'cancel' and 'shutdown' in (outcome.reason, self.cause()):
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
        job_run_summary.start(self.summary, self.reservation)
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
            memory_report.send(self)  # FR-MT-S-4: before the completion call, on every end path
            delivery = Delivery(self.params, self.deps.delivery, self.summary, self._after_report(), cause=self.cause)
            return delivery.deliver(outcome, integrity_ok)
        finally:
            watcher.stop()
