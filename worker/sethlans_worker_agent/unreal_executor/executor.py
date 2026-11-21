# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The Unreal executor (Unreal foundation FR-WX-1, FR-UE-1, FR-UE-2; D1).

Registered through ``executors.get_executor('UNREAL')`` (lazy import).
``capacity_mode = 'unreal_card'``: an Unreal job holds one card (its
reservation's ``card_key``, render per card D1); Blender GPU jobs may run on
the other cards, Blender CPU jobs never beside it (D2).

**``__init__`` is lock-free and starts no threads** (chunk 3 L3 contract:
it runs under the registry's ``_unreal_lock``): it only builds plain
objects; every dependency is resolved at call time.

* ``try_reserve(job_data)``: ``reserve.Reserver`` (FR-UE-1 checks, holds no
  resource on ``None`` or ``ProbePending``).
* ``execute(job_data, ctx)``: ``job_run.JobRun`` for the reservation the
  claim recorded in ``job_data['_renderer_reservation']``; the validated
  ``JobParams`` come from that reservation, never from the raw dict. The
  job's own card stop (``blender_job_stop``, registered at claim) is joined
  with ``ctx.shutdown_event``; the claim's card choice is forgotten when
  ``execute`` returns (``adapter_choice``, keyed by ``claim_id``; C-24 m, D16).

``default_reserve_deps`` / ``default_run_deps`` wire the production
modules; tests pass their own.
"""
import dataclasses
import logging
from typing import Optional

from sethlans_worker_agent import api_extras, api_sequence, config_unreal, unreal_share
from sethlans_worker_agent.blender_job_stop import job_stop
from sethlans_worker_agent.executors.base import ExecutionContext, RenderResult
from sethlans_worker_agent.unreal_executor import adapter_choice, command, job_params, ram_ledger, reserve
from sethlans_worker_agent.unreal_executor.delivery import DeliveryDeps, result

logger = logging.getLogger(__name__)


def _capacity_can_take() -> bool:
    """Check 0: the poll's rule (render per card FR-PC-CAP-5), a free Unreal card from the cached card probe."""
    from sethlans_worker_agent import unreal_bridge
    return unreal_bridge.can_take_unreal()


def _was_shared(job_id) -> bool:
    """D10: another reservation was held on the machine while this job's was (the pilot's ``shared_machine``)."""
    from sethlans_worker_agent import job_processor
    capacity = job_processor.get_worker_capacity()
    return capacity is not None and capacity.was_shared(job_id)


def _capacity():
    from sethlans_worker_agent import job_processor
    return job_processor.get_worker_capacity()


def _status():
    from sethlans_worker_agent.unreal_executor import runtime, startup_recovery
    enabled, reason = runtime.status(runtime.installs())
    if enabled and not startup_recovery.ready():
        return False, "the startup recovery has not succeeded"
    return enabled, reason


def _cache_dir() -> Optional[str]:
    from sethlans_worker_agent.unreal_executor import startup_recovery
    cache = startup_recovery.cache()
    return None if cache is None else str(cache.layout.root)


def _port_ok(slot: int):
    """Check 6a for the reserved slot's port (FR-PC-SLOT-2)."""
    from sethlans_worker_agent.unreal_executor import slot as slots
    from sethlans_worker_agent.unreal_executor import startup_recovery, zen_port
    cache_dir = _cache_dir()
    if cache_dir is None:
        return zen_port.PortCheck(ok=False, error='no cache dir')
    port = slots.slot_for(cache_dir, config_unreal.ZEN_PORT, slot).zen_port
    return zen_port.check_port(port, cache_dir, worker_sid=startup_recovery.worker_sid(), slot=slot)


def _isolation_ok() -> bool:
    from sethlans_worker_agent.unreal_executor import isolation_state
    return isolation_state.passed_within(isolation_state.check_6b_max_age_s())


def _entry_ready(sha256: str) -> bool:
    from sethlans_worker_agent.unreal_executor import startup_recovery
    cache = startup_recovery.cache()
    return cache is not None and cache.state(sha256) == 'ready'


def _unreal_card_keys() -> tuple:
    """The keys of the Unreal cards in use, from the heartbeat's card probe (C-27 b); () when undecided."""
    from sethlans_worker_agent.cards import unreal_cards
    found = unreal_cards.current()
    return tuple(card.key for card in found[0]) if found is not None else ()


class _Lazy:
    """One shared ``Reachability`` (its probe pools start on first use, NF-6)."""
    reach = None

    @classmethod
    def reachability(cls):
        if cls.reach is None:
            from sethlans_worker_agent.project_archive.reachability import Reachability
            cls.reach = Reachability(config_unreal.SHARE_TIMEOUT_SECONDS)
        return cls.reach


def default_reserve_deps() -> reserve.ReserveDeps:
    from sethlans_worker_agent.project_archive.reachability import ProbeBudget, resolve_share_path
    from sethlans_worker_agent.unreal_executor import runtime
    return reserve.ReserveDeps(
        can_take=_capacity_can_take, status=_status, installs=runtime.installs, sampler=runtime.sampler,
        port_ok=_port_ok, isolation_ok=_isolation_ok, share_state=unreal_share.state, entry_ready=_entry_ready,
        reachability=_Lazy.reachability, resolve=resolve_share_path,
        budget=lambda: ProbeBudget(config_unreal.POLL_PROBE_BUDGET_SECONDS), card_keys=_unreal_card_keys,
        ram=ram_ledger.ledger,  # the process's one RAM ledger: the runners publish into it (FR-PC-RAM-5)
        capacity=_capacity, d8_key=adapter_choice.d8_key)


def _worker_id():
    from sethlans_worker_agent import system_monitor
    return system_monitor.WORKER_ID


def _phase_config(slot, merged_names, install):
    """``slot`` is the job's ``slot.Slot``: its port, Zen folders and profile (the hang-CPU exclusion)."""
    from sethlans_worker_agent.unreal_executor import phases, startup_recovery, zen_private
    images = zen_private.zenserver_images([install.root], slot.profile)
    return phases.default_config(slot, startup_recovery.worker_sid(), merged_names, images)


def _phase_runner(config, registry, job, cancel, shutdown, sampler):
    from sethlans_worker_agent.unreal_executor import phases, prelaunch, process_runner, runtime
    return phases.PhaseRunner(config=config, registry=registry, job_dir=job, cancel=cancel, shutdown_event=shutdown,
                              sampler=sampler, run=process_runner.run, prelaunch=prelaunch.check_before_launch,
                              disable_until_restart=runtime.disable_until_restart)


def _flow_config(profile):
    from sethlans_worker_agent.unreal_executor.job_flow import FlowConfig
    from sethlans_worker_agent.unreal_executor.runner_types import MB
    c = config_unreal
    return FlowConfig(step_frames=c.WARMUP_STEP_FRAMES, step_seconds=c.WARMUP_STEP_SECONDS,
                      hold_seconds=c.WARMUP_HOLD_SECONDS, quit_grace_s=c.QUIT_GRACE_SECONDS,
                      log_max_bytes=c.LOG_MAX_MB * MB, profile=str(profile))


def default_run_deps():
    from sethlans_worker_agent.project_archive import limits, workspace
    from sethlans_worker_agent.unreal_executor import (
        job_run, process_runner, profile_config, runtime, startup_recovery, zen_guard)
    return job_run.RunDeps(
        cache=startup_recovery.cache, limits=limits.from_config, reachability=_Lazy.reachability,
        prepare_workspace=workspace.prepare_workspace, zen_scan=zen_guard.scan_project_config,
        engine_cvars=profile_config.count_engine_cvars, sampler=runtime.sampler,
        registry_factory=process_runner.RunnerRegistry, phase_runner=_phase_runner, phase_config=_phase_config,
        launch_context=lambda install, slot: command.context_for_slot(install.editor_cmd, slot),
        builders=command, flow_config=_flow_config, get_assignment=api_extras.get_job_assignment,
        worker_id=_worker_id,
        delivery=DeliveryDeps(release_job=api_extras.release_job, mark_unreachable=unreal_share.mark_unreachable,
                              upload=api_extras.upload_output_handle, report_pilot=api_extras.report_pilot_result,
                              report_shots=api_sequence.report_shots_result, upload_frame=api_sequence.upload_frame,
                              report_piece=api_sequence.report_piece_result, upload_sound=api_sequence.upload_sound,
                              was_shared=_was_shared),
        disable_until_restart=runtime.disable_until_restart, keep_job_logs=lambda: config_unreal.KEEP_JOB_LOGS,
        prep_timeout_s=lambda: config_unreal.PREP_TIMEOUT_SECONDS,
        cancel_poll_s=lambda: config_unreal.CANCEL_POLL_SECONDS, choose_adapter=adapter_choice.choose)


class UnrealExecutor:
    renderer = 'UNREAL'
    capacity_mode = 'unreal_card'

    def __init__(self, reserve_deps: Optional[reserve.ReserveDeps] = None, run_deps=None):
        # Lock-free, no threads: only plain objects (BoundedSemaphore creation acquires nothing).
        self._reserver = reserve.Reserver(reserve_deps or default_reserve_deps())
        self._run_deps = run_deps

    def try_reserve(self, job_data: dict) -> Optional[reserve.UnrealReservation]:
        return self._reserver.try_reserve(job_data)

    def execute(self, job_data: dict, ctx: ExecutionContext) -> RenderResult:
        from sethlans_worker_agent.unreal_executor import job_run
        reservation = job_data.get('_renderer_reservation')
        if not isinstance(reservation, reserve.UnrealReservation):
            logger.error("Unreal job %s has no Unreal reservation; refusing to run it.", job_data.get('id'))
            try:
                job_params.validate_job_data(job_data)
            except job_params.JobDataError as exc:
                return result(error=f"Invalid Unreal job data ({exc}).")
            return result(error="Unreal job was not reserved by this worker's executor.")
        deps = self._run_deps or default_run_deps()
        # The job's own card stop (C-24 m) is seen wherever the shutdown is: the joined event replaces it.
        ctx = dataclasses.replace(ctx, shutdown_event=job_stop(ctx.shutdown_event, job_data))
        try:
            return job_run.JobRun(reservation, ctx, deps).run()
        finally:
            adapter_choice.forget(reservation.claim_id)  # this claim's choice only (D16)
