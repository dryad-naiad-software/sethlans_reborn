# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The UE phases of one Unreal job (Unreal foundation FR-UE-2 steps 5-9,
FR-UE-8, FR-UE-9, FR-UE-10, FR-UE-13, FR-UE-25).

``UeFlow.run()``: prep (PythonScript commandlet, result validation and the
effective-CVar check) -> warm-up when the marker rule asks for it (a pilot
always warms up) -> render -> on ``Compilation cancelled.`` or a forced
quit: drop the pair from the marker, warm up again (``warmup.2.log``) and
re-render **once** (``render.2.log``) -> output collection. A pilot also
checks the D3D12 adapter, collects the CSV capture and computes its
metrics (``pilot.measure``) over **every** phase it ran.

Every launch goes through ``PhaseRunner.launch`` (pre-launch checks, Zen
verification) and every command through the builders in ``command``.
"""
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from sethlans_worker_agent.unreal_executor import (
    cvars, job_dir, log_summary, log_validation, output, pilot, pilot_csv, queue_prep, render_check, warmup)
from sethlans_worker_agent.unreal_executor.outcomes import JobFailure
from sethlans_worker_agent.unreal_executor.runner_types import RENDER_SUCCESS, WARMUP_SUCCESS

logger = logging.getLogger(__name__)


@dataclass
class FlowConfig:
    step_frames: int
    step_seconds: int
    hold_seconds: int
    quit_grace_s: float
    log_max_bytes: int
    profile: str


@dataclass
class FlowResult:
    prep: queue_prep.PrepResult
    render_s: float
    prep_s: float
    warmup_s: float
    warmup_ran: bool
    render_time_seconds: int
    outputs: List[Path] = field(default_factory=list)
    metrics: Optional[pilot.PilotMetrics] = None
    adapter: str = ''
    gate_reading: object = None


class UeFlow:

    def __init__(self, params, launch_ctx, job, runner, summary, builders, marker_path, uproject, config: FlowConfig,
                 reading=None):
        self.params, self.ctx, self.job, self.runner = params, launch_ctx, job, runner
        self.summary, self.builders, self.marker, self.uproject = summary, builders, marker_path, str(uproject)
        self.config, self.reading = config, reading
        self.cvars_sha = cvars.cvars_sha256(params.merged)
        self.prep_s = self.warmup_s = self.render_s = 0.0

    # -- prep -----------------------------------------------------------
    def prep(self) -> queue_prep.PrepResult:
        params, job = self.params, self.job
        queue_prep.write_json(job.params, queue_prep.prep_params(params, job.output, job.prep_result))
        launch = self.builders.build_prep(self.ctx, self.uproject, job.script(job_dir.PREP_SCRIPT),
                                          job.log('prep', 1), job.params, params.merged)
        run = self.runner.launch('prep', 1, launch, success_check=lambda: queue_prep.result_ok(job.prep_result),
                                 sample=params.is_pilot)
        self.prep_s = run.outcome.wall_s
        self.summary.set('prep_s', round(self.prep_s, 1))
        self._note_quit(run, 'prep')
        render_check.check_prep(run.outcome)
        try:
            prep = queue_prep.read_result(job.prep_result, params)
        except queue_prep.PrepError as exc:
            if run.outcome.exit_code not in (0, None) and queue_prep.load(job.prep_result) is None:
                raise JobFailure(f"Unreal prep pass failed (exit code {run.outcome.exit_code}).") from exc
            raise JobFailure(str(exc)) from exc
        error, overridden = cvars.check_effective(prep.effective_cvars, params.merged)
        if error:
            raise JobFailure(error)
        for name in overridden:
            self.summary.append('cvar_overridden', name)
            self.summary.warn(f"cvar_overridden={name}")
        for text in prep.warnings:
            self.summary.warn(text)
        self.summary.update({'preset_kind': prep.preset_kind, 'queue': prep.queue_asset})
        if params.is_pilot:
            self.summary.set('pilot_frames', len(prep.pilot_frames))
        return prep

    # -- warm-up --------------------------------------------------------
    def warm_up(self, prep, attempt: int):
        params, job, cfg = self.params, self.job, self.config
        data = queue_prep.warmup_params(params, prep, job.warmup_result, cfg.step_frames, cfg.step_seconds,
                                        cfg.hold_seconds)
        queue_prep.write_json(job.warmup_params, data)
        launch = self.builders.build_warmup(self.ctx, self.uproject, job.script(job_dir.WARMUP_SCRIPT),
                                            job.log('warmup', attempt), job.warmup_params, params.merged)
        run = self.runner.launch('warmup', attempt, launch, success_marker=WARMUP_SUCCESS, sample=params.is_pilot)
        self.warmup_s += run.outcome.wall_s
        self._note_quit(run, 'warmup')
        render_check.check_warmup(run.outcome)
        warmup.record_success(self.marker, params.map, params.sequence, self.cvars_sha)
        return run

    # -- render ---------------------------------------------------------
    def render(self, prep, attempt: int):
        params, job = self.params, self.job
        launch = self.builders.build_render(self.ctx, self.uproject, params.map, prep.queue_asset,
                                            job.log('render', attempt), params.merged, pilot_launch=params.is_pilot)
        before = pilot_csv.listing(self.config.profile) if params.is_pilot else frozenset()
        run = self.runner.launch('render', attempt, launch, success_marker=RENDER_SUCCESS, sample=True)
        self.render_s += run.outcome.wall_s
        self._note_quit(run, 'render')
        check = render_check.check_render(run.outcome, run.log_path, prep.queue_jobs, self.config.log_max_bytes)
        return run, check, before

    def _note_quit(self, run, phase: str) -> None:
        if run.outcome.quit_forced:
            self.summary.append('quit_forced', phase)
        if run.outcome.fatal_rule == 'log_flood' or run.outcome.rule_counts.get('log_flood'):
            self.summary.set('log_flood', phase)

    # -- sequence -------------------------------------------------------
    def _warm_if_needed(self, prep) -> bool:
        reason = warmup.decide(self.marker, self.params, self.cvars_sha)
        if reason is None:
            self.summary.set('warmup', 'skipped')
            return False
        logger.info("Unreal job %s: warm-up runs (%s).", self.params.job_id, reason)
        self.warm_up(prep, 1)
        self.summary.set('warmup', 'ran')
        return True

    def _render_with_retry(self, prep):
        run, check, before = self.render(prep, 1)
        if check.retry:
            logger.warning("Unreal job %s: render needs the FR-UE-10 retry (%s).", self.params.job_id, check.retry)
            warmup.remove_pair(self.marker, self.params.map, self.params.sequence)
            self.warm_up(prep, 2)
            self.summary.set('warmup', 'ran(retry)')
            run, check, before = self.render(prep, 2)
            if check.retry:
                self._render_summary(run, check)
                raise render_check.retry_failure(check, self.config.quit_grace_s)
        return run, check, before

    def _render_summary(self, run, check) -> None:
        values, warnings = log_summary.render_values(check.scan, self.params.merged)
        self.summary.update(values)
        for text in warnings:
            self.summary.warn(text)
        self.summary.update({'render_exit': run.outcome.exit_code, 'render_s': round(self.render_s, 1)})
        self.summary.update(log_summary.vram_values(self.params, run.outcome.vram_peak_mb))

    def run(self) -> FlowResult:
        prep = self.prep()
        warmed = self._warm_if_needed(prep)
        run, check, before = self._render_with_retry(prep)
        warmed = warmed or self.summary.get('warmup') == 'ran(retry)'
        self._render_summary(run, check)
        self.summary.set('warmup_s', round(self.warmup_s, 1) if warmed else '')
        result = FlowResult(prep=prep, render_s=self.render_s, prep_s=self.prep_s, warmup_s=self.warmup_s,
                            warmup_ran=warmed, render_time_seconds=render_check_time(check, self.render_s),
                            gate_reading=self.reading)
        if not self.params.is_pilot:
            result.outputs = [output.collect_single(self.job.output)]
            self.summary.set('output', result.outputs[0].name)
            return result
        result.outputs = output.collect_pilot(self.job.output, self.params.job_id, prep.pilot_frames)
        result.adapter = pilot.check_adapter(run.log_path, self.reading)
        csv_peak = pilot_csv.collect(run.log_path, self.config.profile, before, self.job.logs)
        result.metrics = pilot.measure(self.runner.runs, csv_peak)
        self.summary.update(pilot.summary_values(result.metrics, list(prep.pilot_frames), result.adapter,
                                                 self.cvars_sha))
        return result


def render_check_time(check, render_s: float) -> int:
    """FR-UE-15: the parsed MRQ duration, else the render wall time (prep, warm-up and copy excluded)."""
    return log_validation.render_time_seconds(check.scan, render_s)
