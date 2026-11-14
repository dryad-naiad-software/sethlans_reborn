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
   ``phase_vram_sample(sampler)``;
4. the post-exit reading; survivors raise ``SurvivorsLeft``; the loopback
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

from sethlans_worker_agent.unreal_executor import log_validation, zen_log, zen_port
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
                                worker_sid=cfg.worker_sid)
        if not result.ok:
            raise LocalConflict(result.release_reason, result.message)
        self.profile_reset += result.profile_reset
        self.user_cvars = max(self.user_cvars, result.user_cvars)
        self.collisions.extend(c for c in result.user_cvar_collisions if c not in self.collisions)

    def reading(self) -> Optional[object]:
        if self.sampler is None:
            return None
        return self.sampler.fresh(max_age_s=1.0, deadline=time.monotonic() + READING_TIMEOUT_S)

    @staticmethod
    def used(reading) -> Optional[Dict[int, int]]:
        return None if reading is None else {gpu.index: gpu.used_mb for gpu in reading.gpus}

    def _spec(self, launch, phase, attempt, success_marker, success_check, sample, baseline) -> RunSpec:
        cfg = self.config
        return RunSpec(
            launch=launch, log_path=self.job_dir.log(phase, attempt), stdout_path=self.job_dir.stdout(phase, attempt),
            ledger_path=self.job_dir.ledger, timeout_s=cfg.timeouts[phase], hang_s=cfg.hang_s,
            quit_grace_s=cfg.quit_grace_s, log_max_bytes=cfg.log_max_bytes,
            log_flood_bytes_per_min=cfg.log_flood_bytes_per_min,
            fatal_rules=log_validation.fatal_rules(), success_marker=success_marker, success_check=success_check,
            cancel=self.cancel, shutdown_event=self.shutdown_event, excluded_images=cfg.excluded_images,
            vram_sample=phase_vram_sample(self.sampler) if sample and self.sampler is not None else None,
            tick_check=self.loopback_factory(cfg.zen_port, cfg.cache_dir, worker_sid=cfg.worker_sid),
            vram_baseline=baseline)

    def launch(self, phase: str, attempt: int, launch, *, success_marker=None, success_check=None,
               sample: bool = False) -> PhaseRun:
        self._stop_requested()
        self._guard()
        self._prelaunch()
        self.first_launch = False
        pre_reading = self.reading() if sample else None
        pre = self.used(pre_reading)
        spec = self._spec(launch, phase, attempt, success_marker, success_check, sample, pre or ({} if sample else None))
        outcome = self.run(spec, self.registry)
        run = PhaseRun(phase, attempt, outcome, spec.log_path, pre=pre, pre_reading=pre_reading)
        self.runs.append(run)
        if outcome.survivors:
            raise SurvivorsLeft(outcome.survivors)
        run.post = self.used(self.reading()) if sample else None
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
                                 max_bytes=cfg.log_max_bytes)
        if not result.ok:
            logger.error("Zen log verification failed in %s: %s", run.phase, result.detail)
        return result.ok

    @property
    def zen_ok(self) -> bool:
        return all(run.zen_ok for run in self.runs)


def default_config(cache_dir, worker_sid, merged_names, excluded_images=frozenset()) -> PhaseConfig:
    from sethlans_worker_agent import config_unreal as c
    return PhaseConfig(
        cache_dir=str(cache_dir), zen_port=c.ZEN_PORT, worker_sid=worker_sid, merged_names=list(merged_names),
        timeouts={'prep': c.PREP_TIMEOUT_SECONDS, 'warmup': c.WARMUP_TIMEOUT_SECONDS,
                  'render': c.RENDER_TIMEOUT_SECONDS},
        hang_s=c.HANG_SECONDS, quit_grace_s=c.QUIT_GRACE_SECONDS, log_max_bytes=c.LOG_MAX_MB * MB,
        log_flood_bytes_per_min=c.LOG_FLOOD_MB_PER_MIN * MB, excluded_images=excluded_images)
