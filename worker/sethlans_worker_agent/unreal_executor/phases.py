# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
One UE launch of a job (Unreal foundation FR-UE-2, FR-UE-11, FR-UE-25,
FR-UE-26, FR-UE-27; chunk 7 integration requirements 10, 11, 15).

``PhaseRunner.launch(phase, attempt, launch, ...)`` runs, in order:

0. no launch once a cancel cause or the shutdown is set (``Cancelled``);
   ``zen_guard.scan_project_config`` again on the working copy (an earlier
   phase ran project code), a hit failing the job (FR-UE-24);
1. ``prelaunch.check_before_launch`` (the one pre-launch function: profile
   repair, the known-folder probe before the job's **first** launch, the
   farm-profile config check, the Zen port re-check). A failure raises
   ``LocalConflict`` (``dispatch_failed`` before the first launch, else
   ``local_conflict``);
2. a pre-launch GPU reading when the phase samples GPU memory (every pilot
   phase, and every render);
3. ``process_runner.run(spec, registry)`` with a **fresh**
   ``zen_port.LoopbackWatch`` as ``tick_check`` and
   ``phase_vram_sample(sampler)``. Every reading of the launch (pre-launch,
   per tick, post-exit) is cut to the launch's card (D22, FR-GU-11,
   ``vram.only_card``): the ``adapter`` choice's key, else the gated card
   (``card_key``, set by ``job_run``; a pilot's prep has no choice). A
   reading without that card counts as no sample. With no key at all (the
   override) nothing is cut;
4. a warm-up or render whose log names another D3D12 adapter than its
   ``-graphicsadapter`` card raises the alert and refuses the project
   (``adapter_log.check_run``), then releases the job unless survivors
   remain (FR-CU-3);
   the post-exit reading; survivors raise ``SurvivorsLeft``; the loopback
   rule raises ``LocalConflict`` and disables Unreal until restart; a cancel
   cause raises ``Cancelled``;
5. the FR-UE-26 Zen log verification (data path, port, override line),
   failing with ``zen=FAILED``.

The ``RunOutcome`` and readings are returned for the phase-specific checks.
"""
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from sethlans_worker_agent.unreal_executor import adapter_log, log_validation, vram, zen_log, zen_port
from sethlans_worker_agent.unreal_executor.outcomes import Cancelled, JobFailure, LocalConflict, SurvivorsLeft
from sethlans_worker_agent.unreal_executor.process_runner import phase_vram_sample
from sethlans_worker_agent.unreal_executor.runner_types import CANCEL_CAUSES, MB, RunSpec

logger = logging.getLogger(__name__)

READING_TIMEOUT_S = 1.5


@dataclass
class PhaseRun:
    phase: str
    attempt: int
    outcome: object
    log_path: object
    pre: Optional[Dict[int, int]] = None
    post: Optional[Dict[int, int]] = None
    pre_reading: object = None
    zen_ok: bool = True


@dataclass
class PhaseConfig:
    cache_dir: str
    zen_port: int
    worker_sid: Optional[str]
    merged_names: List[str]
    timeouts: Dict[str, float]
    hang_s: float
    quit_grace_s: float
    log_max_bytes: int
    log_flood_bytes_per_min: int
    excluded_images: frozenset = frozenset()
    slot: int = 0       # the job's Unreal slot (FR-PC-SLOT-2); zen_port is that slot's port


@dataclass
class PhaseRunner:
    config: PhaseConfig
    registry: object
    job_dir: object
    cancel: Callable[[], Optional[str]]
    shutdown_event: object
    sampler: Optional[object]
    run: Callable
    prelaunch: Callable
    verify_zen: Callable = zen_log.verify_log
    loopback_factory: Callable = zen_port.LoopbackWatch
    disable_until_restart: Callable[[str], None] = lambda reason: None
    first_launch: bool = True
    profile_reset: int = 0
    user_cvars: int = 0
    collisions: List[str] = field(default_factory=list)
    runs: List[PhaseRun] = field(default_factory=list)
    project_dir: object = None              # the working copy's project folder (set by job_run)
    zen_scan: Optional[Callable] = None     # zen_guard.scan_project_config, re-run before every launch
    add_alert: Optional[Callable] = None    # store.add_alert (adapter_log's default when None)
    project: str = ''                       # archive sha256 (set by job_run): an adapter mismatch refuses it
    card_key: Optional[str] = None          # the gated card (set by job_run): traces of a launch without a choice

    def _stop_requested(self) -> None:
        """No launch once a cancel cause or the shutdown is known (review L2)."""
        if self.shutdown_event is not None and self.shutdown_event.is_set():
            raise Cancelled('shutdown')
        cause = self.cancel() if self.cancel is not None else None
        if cause:
            raise Cancelled(cause)

    def _guard(self) -> None:
        """FR-UE-24 again before every launch: an earlier phase ran project code that can write ini files (W5)."""
        if self.zen_scan is None or self.project_dir is None:
            return
        result = self.zen_scan(self.project_dir)
        if not result.ok:
            raise JobFailure(result.message)

    def _prelaunch(self) -> None:
        cfg = self.config
        result = self.prelaunch(cfg.cache_dir, cfg.zen_port, cfg.merged_names, self.first_launch,
                                worker_sid=cfg.worker_sid, slot=cfg.slot)
        if not result.ok:
            raise LocalConflict(result.release_reason, result.message)
        self.profile_reset += result.profile_reset
        self.user_cvars = max(self.user_cvars, result.user_cvars)
        self.collisions.extend(c for c in result.user_cvar_collisions if c not in self.collisions)

    def reading(self, key: Optional[str] = None) -> Optional[object]:
        """A fresh reading, cut to the card ``key`` when one is given (FR-GU-11)."""
        if self.sampler is None:
            return None
        return _cut(self.sampler.fresh(max_age_s=1.0, deadline=time.monotonic() + READING_TIMEOUT_S), key)

    def _key(self, adapter) -> Optional[str]:
        return adapter.key if adapter is not None else self.card_key

    @staticmethod
    def used(reading) -> Optional[Dict[int, int]]:
        return None if reading is None else {gpu.index: gpu.used_mb for gpu in reading.gpus}

    def _spec(self, launch, phase, attempt, success_marker, success_check, sample, baseline, adapter) -> RunSpec:
        cfg = self.config
        sampled = phase_vram_sample(self.sampler) if sample and self.sampler is not None else None
        key = self._key(adapter)
        return RunSpec(
            launch=launch, log_path=self.job_dir.log(phase, attempt), stdout_path=self.job_dir.stdout(phase, attempt),
            ledger_path=self.job_dir.ledger, timeout_s=cfg.timeouts[phase], hang_s=cfg.hang_s,
            quit_grace_s=cfg.quit_grace_s, log_max_bytes=cfg.log_max_bytes,
            log_flood_bytes_per_min=cfg.log_flood_bytes_per_min,
            fatal_rules=log_validation.fatal_rules(), success_marker=success_marker, success_check=success_check,
            cancel=self.cancel, shutdown_event=self.shutdown_event, excluded_images=cfg.excluded_images,
            vram_sample=(lambda timeout_s: _cut(sampled(timeout_s), key)) if sampled is not None else None,
            tick_check=self.loopback_factory(cfg.zen_port, cfg.cache_dir, worker_sid=cfg.worker_sid, slot=cfg.slot),
            vram_baseline=baseline, expected_adapter=adapter.index if adapter is not None else None,
            expected_ids=adapter_log.ids_of(adapter))

    def launch(self, phase: str, attempt: int, launch, *, success_marker=None, success_check=None,
               sample: bool = False, adapter=None) -> PhaseRun:
        """``adapter``: the ``adapter_choice.Choice`` of a warm-up or render (its index is on the command line)."""
        self._stop_requested()
        self._guard()
        self._prelaunch()
        self.first_launch = False
        pre_reading = self.reading(self._key(adapter)) if sample else None
        pre = self.used(pre_reading)
        spec = self._spec(launch, phase, attempt, success_marker, success_check, sample,
                          pre or ({} if sample else None), adapter)
        outcome = self.run(spec, self.registry)
        run = PhaseRun(phase, attempt, outcome, spec.log_path, pre=pre, pre_reading=pre_reading)
        self.runs.append(run)
        conflict = adapter_log.check_run(outcome, adapter, self)   # alert and per-project refusal done
        if outcome.survivors:
            raise SurvivorsLeft(outcome.survivors)
        if conflict is not None:
            raise conflict
        run.post = self.used(self.reading(self._key(adapter))) if sample else None
        if outcome.reason == 'local_conflict':
            self.disable_until_restart(zen_port.LOOPBACK_DISABLE_REASON)
            raise LocalConflict('local_conflict', outcome.detail or zen_port.LOOPBACK_DISABLE_REASON)
        if outcome.reason in CANCEL_CAUSES:
            raise Cancelled(outcome.reason)
        run.zen_ok = self._verify_zen(run)
        if not run.zen_ok and outcome.reason in ('exited', 'exited_after_grace'):
            raise JobFailure(zen_log.FAILED_MESSAGE)  # a failed phase keeps its own, more specific message
        return run

    def _verify_zen(self, run: PhaseRun) -> bool:
        if run.outcome.fatal_rule == log_validation.LOG_FLOOD:
            return True  # the flood message is the better error; the log may be too large to read
        cfg = self.config
        result = self.verify_zen(run.log_path, cfg.cache_dir, cfg.zen_port, run.phase,
                                 max_bytes=cfg.log_max_bytes, slot=cfg.slot)
        if not result.ok:
            logger.error("Zen log verification failed in %s: %s", run.phase, result.detail)
        return result.ok

    @property
    def zen_ok(self) -> bool:
        return all(run.zen_ok for run in self.runs)


def _cut(reading, key: Optional[str]):
    """The reading with only the card ``key`` (``None``: no sample); unchanged without a key (the override)."""
    return reading if key is None else vram.only_card(reading, key)


def default_config(slot, worker_sid, merged_names, excluded_images=frozenset()) -> PhaseConfig:
    """The phases' settings for ``slot`` (a ``slot.Slot``: its cache, port and number)."""
    from sethlans_worker_agent import config_unreal as c
    return PhaseConfig(
        cache_dir=str(slot.cache_dir), zen_port=slot.zen_port, slot=slot.n, worker_sid=worker_sid,
        merged_names=list(merged_names),
        timeouts={'prep': c.PREP_TIMEOUT_SECONDS, 'warmup': c.WARMUP_TIMEOUT_SECONDS,
                  'render': c.RENDER_TIMEOUT_SECONDS},
        hang_s=c.HANG_SECONDS, quit_grace_s=c.QUIT_GRACE_SECONDS, log_max_bytes=c.LOG_MAX_MB * MB,
        log_flood_bytes_per_min=c.LOG_FLOOD_MB_PER_MIN * MB, excluded_images=excluded_images)
