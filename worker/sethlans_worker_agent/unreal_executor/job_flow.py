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
re-render **once** (``render.2.log``) -> output collection. A pilot's
measurement step is ``pilot_flow.collect``: the card it rendered on must be
in the gate's GPU memory reading, by card key (``pilot.check_card``,
FR-GU-4); it is released when a warm-up or render log lacks UE's ``Chosen``
adapter line (``require_adapter_lines``; normal jobs keep ``adapter_log``'s
rule); it collects the CSV capture, computes its metrics and D6 verdict
(``pilot.measure``) over **every** phase it ran and reads the driver version
(per-machine memory test FR-MT-V-1). A pilot whose warm-up or render fails
after a saturated sample reports ``does_not_fit`` instead of failing
(``pilot_flow.after_failure``, D6; an ``IsolationFailure`` is raised again).
Either way, other programs' use of the card (Q10, ``pilot_flow.check_others``) is
judged in three bands against ``unreal.pilot_others_margin_mb`` (default 512): a rise up to the margin leaves
the result as measured; after a larger rise while the card stayed clear of full (no sample at its 93% line),
the part of other programs' use the measurement does not already hold is added to the peak before D6's
verdict (``pilot_others``); a larger rise when the card came close to full releases the pilot
with no verdict. ``result_peak_mb`` (set by ``job_run``) is the peak of the machine result the gate chose
(FR-MT-G-4's summary).
A sequence piece (``sequence_flow.RangeFlow``) and an audio job
(``audio_flow.AudioFlow``, whose render also gets ``_render_extra``) replace four hooks.

Every launch goes through ``PhaseRunner.launch`` (pre-launch checks, Zen
verification) and every command through the builders in ``command``. GPU
memory is sampled on every warm-up and render launch (the prep only for a
pilot): a cold warm-up can peak above the render, and a job's memory report
counts its warm-up launches (per-machine memory test Q11, ``job_memory.job_peak_mb``). Each
warm-up and render launch resolves its card's DXGI index afresh (``choose``,
``adapter_choice.choose``; FR-CU-1), always the card of the first launch (D8).
The first launch asks for the gated card (``first_key``, FR-GU-10; ``None``
with the override): it renders there or ``choose``'s ``LocalConflict`` releases it.
"""
import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import List, Optional

from sethlans_worker_agent.unreal_executor import (  # output, pilot, pilot_csv: tests patch them through here
    adapter_choice, cvars, job_dir, log_summary, log_validation, output, pilot, pilot_csv, pilot_flow, queue_prep,
    render_check, warmup)
from sethlans_worker_agent.unreal_executor.outcomes import JobFailure
from sethlans_worker_agent.unreal_executor.pilot_flow import NO_ADAPTER_LINE_MESSAGE, require_adapter_lines
from sethlans_worker_agent.unreal_executor.runner_types import RENDER_SUCCESS, WARMUP_SUCCESS

logger = logging.getLogger(__name__)
__all__ = ['FlowConfig', 'FlowResult', 'NO_ADAPTER_LINE_MESSAGE', 'UeFlow', 'pilot', 'pilot_csv',
           'render_check_time', 'require_adapter_lines']


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
    card: object = None           # pilot: the reading's GpuMem of the card it rendered on (FR-GU-4)
    piece: object = None          # a sequence piece: sequence_flow.PieceOutput (frames, timings, card)
    sound: object = None          # an audio job: audio_flow.SoundOutput (the .wav, its facts, the offset)
    ram: Optional[dict] = None    # pilot: the body's RAM figures per phase (FR-PC-RAM-3); None when unknown
    ram_process_commit_peak_mb: Optional[int] = None
    driver_version: Optional[str] = None  # pilot: the render launch's driver version (FR-MT-V-1); None unknown
    shared_peak_mb: Optional[int] = None  # pilot: its own largest shared usage over the render launch (D9)


class UeFlow:
    _render_extra: dict = {}      # extra build_render keywords (an audio job: the audio flags)
    result_peak_mb: Optional[int] = None  # the gate's machine result peak (set by job_run; FR-MT-G-4)

    def __init__(self, params, launch_ctx, job, runner, summary, builders, marker_path, uproject, config: FlowConfig,
                 reading=None, choose=None, first_key=None):
        self.params, self.ctx, self.job, self.runner = params, launch_ctx, job, runner
        self.summary, self.builders, self.marker, self.uproject = summary, builders, marker_path, str(uproject)
        self.config, self.reading = config, reading
        self.cvars_sha = cvars.warmup_cvars_sha256(params.merged)  # the warm-up key: shader-relevant CVars only
        self.prep_s = self.warmup_s = self.render_s = 0.0
        self.choose, self.choice, self.first_key = choose or adapter_choice.choose, None, first_key
        self.warmup_logs: List[tuple] = []  # (card key, log) of each warm-up launch (the driver's fallback log)

    def _adapter_ctx(self):
        """FR-CU-1: the job's card (the gated one at the first launch) re-resolved; its DXGI index on the command line."""
        self.choice = self.choose(self.params.job_id,
                                  key=self.choice.key if self.choice is not None else self.first_key)
        return replace(self.ctx, graphics_adapter=self.choice.index)

    # -- hooks: prep params, warm-up frames, render check, output step (sequence_flow.RangeFlow replaces them) --
    def _prep_params(self) -> dict:
        return queue_prep.prep_params(self.params, self.job.output, self.job.prep_result, self.job.scripts)

    def _warm_frames(self, prep) -> Optional[List[int]]:
        return None  # the pilot's frames, or the job's one frame (queue_prep.warmup_params)

    def _check_render(self, run, prep):
        return render_check.check_render(run.outcome, run.log_path, prep.queue_jobs, self.config.log_max_bytes)

    # -- prep -----------------------------------------------------------
    def prep(self) -> queue_prep.PrepResult:
        params, job = self.params, self.job
        queue_prep.write_json(job.params, self._prep_params())
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
        # What the render runs with (D23, FR-GC-5): shown only; the worker never changes it (FR-GC-6).
        self.summary.update({'preset_kind': prep.preset_kind, 'game_overrides': prep.game_overrides,
                             'texture_streaming': prep.texture_streaming, 'queue': prep.queue_asset})
        if prep.game_overrides == 'implicit':
            self.summary.warn(queue_prep.IMPLICIT_WARNING)
        if params.is_pilot:
            self.summary.set('pilot_frames', len(prep.pilot_frames))
        return prep

    # -- warm-up --------------------------------------------------------
    def warm_up(self, prep, attempt: int):
        params, job, cfg = self.params, self.job, self.config
        data = queue_prep.warmup_params(params, prep, job.warmup_result, cfg.step_frames, cfg.step_seconds,
                                        cfg.hold_seconds, frames=self._warm_frames(prep))
        queue_prep.write_json(job.warmup_params, data)
        launch = self.builders.build_warmup(self._adapter_ctx(), self.uproject, job.script(job_dir.WARMUP_SCRIPT),
                                            job.log('warmup', attempt), job.warmup_params, params.merged)
        self.warmup_logs.append((self.choice.key, job.log('warmup', attempt)))
        run = self.runner.launch('warmup', attempt, launch, success_marker=WARMUP_SUCCESS, sample=True,
                                 adapter=self.choice)  # Q11: a job's own warm-up peak feeds its memory report
        self.warmup_s += run.outcome.wall_s
        self._note_quit(run, 'warmup')
        render_check.check_warmup(run.outcome)
        warmup.record_success(self.marker, params.map, params.sequence, self.cvars_sha)
        return run

    # -- render ---------------------------------------------------------
    def render(self, prep, attempt: int):
        params, job = self.params, self.job
        launch = self.builders.build_render(self._adapter_ctx(), self.uproject, params.map, prep.queue_asset,
                                            job.log('render', attempt), params.merged, pilot_launch=params.is_pilot,
                                            **self._render_extra)
        before = pilot_csv.listing(self.config.profile) if params.is_pilot else frozenset()
        run = self.runner.launch('render', attempt, launch, success_marker=RENDER_SUCCESS, sample=True,
                                 adapter=self.choice)
        self.render_s += run.outcome.wall_s
        self._note_quit(run, 'render')
        return run, self._check_render(run, prep), before

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
            job_dir.clear_output(self.job.output)  # attempt 1's files never mix with the retry's
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
        self.summary.update(log_summary.vram_values(self.params, run.outcome.vram_peak_mb, self.result_peak_mb))

    def run(self) -> FlowResult:
        prep = self.prep()
        try:
            warmed = self._warm_if_needed(prep)
            run, check, before = self._render_with_retry(prep)
        except JobFailure as failure:  # D6: a pilot that failed after a saturated sample does not fit (else re-raised)
            failed = FlowResult(prep=prep, render_s=self.render_s, prep_s=self.prep_s, warmup_s=self.warmup_s,
                                warmup_ran=bool(self.warmup_logs), render_time_seconds=int(self.render_s),
                                gate_reading=self.reading)
            return pilot_flow.after_failure(self, prep, failure, failed)
        warmed = warmed or self.summary.get('warmup') == 'ran(retry)'
        self._render_summary(run, check)
        self.summary.set('warmup_s', round(self.warmup_s, 1) if warmed else '')
        result = FlowResult(prep=prep, render_s=self.render_s, prep_s=self.prep_s, warmup_s=self.warmup_s,
                            warmup_ran=warmed, render_time_seconds=render_check_time(check, self.render_s),
                            gate_reading=self.reading)
        return self._collect(result, prep, run, before)

    def _collect(self, result: FlowResult, prep, run, before) -> FlowResult:  # one PNG, or a pilot's and its measure
        if not self.params.is_pilot:
            result.outputs = [output.collect_single(self.job.output)]
            self.summary.set('output', result.outputs[0].name)
            return result
        return pilot_flow.collect(self, result, prep, run, before)


def render_check_time(check, render_s: float) -> int:
    """FR-UE-15: the parsed MRQ duration, else the render wall time (prep, warm-up and copy excluded)."""
    return log_validation.render_time_seconds(check.scan, render_s)
