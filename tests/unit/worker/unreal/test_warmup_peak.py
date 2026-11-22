# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A job's warm-up launch is sampled and counts toward its memory report (Unreal per-machine memory test v2, Q11, D6).

Every warm-up launch samples GPU memory on its card (``UeFlow.warm_up``: ``sample=True``), for render, piece
and audio jobs alike; a job's prep, the shots prep and the audio export step stay unsampled (a pilot samples
its prep as before). ``job_memory.job_peak_mb`` is the largest ``VramTrace.max_job`` over the launches whose
phase is in ``PEAK_PHASES`` (warm-up and render; the prep never counts), so a cold warm-up that peaks above the
render is the report's ``job_peak_mb``. Unchanged: a warm-up trace gets no spill watch (only a sampled render
does) and the summary's ``vram_job_peak_mb`` is still the last render launch's. When the report is due and
``vram_job_peak_phase`` are in ``test_warmup_report``.
"""
import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Optional

import pytest

from sethlans_worker_agent.unreal_executor import job_flow, job_memory, memory_report, phases, spill
from sethlans_worker_agent.unreal_executor.adapter_choice import Choice
from sethlans_worker_agent.unreal_executor.prelaunch import PreLaunch
from sethlans_worker_agent.unreal_executor.runner_types import VramTrace
from sethlans_worker_agent.unreal_executor.summary import Summary
from sethlans_worker_agent.unreal_executor.zen_log import ZenLogResult
from tests.unit.worker.unreal._executor_fakes import outcome, params
from tests.unit.worker.unreal._memory_report_fakes import job_run, render_run
from tests.unit.worker.unreal.sequence._pieces import piece_params
from tests.unit.worker.unreal.sequence.test_audio_flow import _flow as audio_flow
from tests.unit.worker.unreal.sequence.test_range_flow import CARD as PIECE_CARD, KEY as PIECE_KEY, _flow as range_flow
from tests.unit.worker.unreal.sequence.test_shots_job import _flow as shots_flow
from tests.unit.worker.unreal.test_phases_card_cut import CHOICE, JOB_KEY, JOB_PID, Job, Sampler, card, other, script
from tests.unit.worker.unreal.test_spill_wiring import gate, phase_runner as spill_runner, two_cards

PREP = {'ok': True, 'queue_asset': '/Game/_SethlansFarm/Job42/Q_Job42', 'preset_kind': 'graph',
        'playback_range': [0, 1928], 'camera_cut_starts': [0, 99], 'effective_cvars': {}, 'queue_jobs': 1,
        'warnings': []}


@dataclass(frozen=True)
class Ctx:
    graphics_adapter: Optional[int] = None


class Runner:
    """Records each launch's keywords; the prep writes a valid prep result."""

    def __init__(self, job, prep):
        self.job, self.prep, self.launches, self.runs = job, prep, [], []

    def launch(self, phase, attempt, launch, **kwargs):
        self.launches.append((phase, attempt, kwargs))
        if phase == 'prep':
            self.job.prep_result.write_text(json.dumps(self.prep), encoding='utf-8')
        return SimpleNamespace(phase=phase, outcome=outcome(wall_s=3.0), log_path=self.job.log(phase, attempt))


def ue_flow(tmp_path, kind='render'):
    """A ``UeFlow`` (a standalone render or a pilot) over the recording runner."""
    (tmp_path / 'scripts').mkdir(exist_ok=True)
    job = SimpleNamespace(params=tmp_path / 'params.json', prep_result=tmp_path / 'prep_result.json',
                          warmup_params=tmp_path / 'warmup_params.json', warmup_result=tmp_path / 'warmup_result.json',
                          scripts=tmp_path / 'scripts', output=tmp_path / 'output',
                          script=lambda name: tmp_path / 'scripts' / name,
                          log=lambda phase, attempt: tmp_path / f'{phase}.{attempt}.log')
    prep = dict(PREP, pilot_frames=[0, 99], queue_jobs=2) if kind == 'pilot' else PREP
    runner = Runner(job, prep)
    builders = SimpleNamespace(build_prep=lambda *a: ('prep', a), build_warmup=lambda *a: ('warmup', a))
    config = job_flow.FlowConfig(step_frames=24, step_seconds=1, hold_seconds=1, quit_grace_s=5.0,
                                 log_max_bytes=1 << 20, profile=str(tmp_path / 'profile'))
    flow = job_flow.UeFlow(params(kind=kind), Ctx(), job, runner, Summary(), builders, tmp_path / 'marker.json',
                           'Demo.uproject', config, choose=lambda job_id, key=None: CHOICE, first_key=JOB_KEY)
    return flow, runner


def sampled(launches):
    """``(phase, attempt) -> sample`` of every recorded launch (``(phase, attempt, launch?, kwargs)`` tuples)."""
    return [((entry[0], entry[1]), entry[-1]['sample']) for entry in launches]


class TestWarmUpLaunchIsSampled:

    @pytest.mark.parametrize('attempt', [1, 2], ids=['first', 'retry'])
    def test_a_render_jobs_warm_up_samples_on_its_card(self, tmp_path, attempt):
        flow, runner = ue_flow(tmp_path)
        flow.warm_up(flow.prep(), attempt)
        assert sampled(runner.launches) == [(('prep', 1), False), (('warmup', attempt), True)]
        assert runner.launches[1][2]['adapter'] is CHOICE   # the reading is cut to the job's card

    def test_a_pilot_still_samples_its_prep_and_warm_up(self, tmp_path):
        flow, runner = ue_flow(tmp_path, kind='pilot')
        flow.warm_up(flow.prep(), 1)
        assert sampled(runner.launches) == [(('prep', 1), True), (('warmup', 1), True)]

    def test_a_pieces_warm_up_is_sampled(self, tmp_path):
        flow, runner, _job = range_flow(tmp_path, piece_params())
        flow.run()
        assert sampled(runner.launches) == [(('prep', 1), False), (('warmup', 1), True), (('render', 1), True)]
        assert runner.launches[1][3]['adapter'] == Choice(42, PIECE_KEY, PIECE_CARD.name, 1)

    def test_an_audio_jobs_warm_up_is_sampled_and_its_export_step_is_not(self, tmp_path):
        flow, runner, _job = audio_flow(tmp_path)
        flow.run()
        assert sampled(runner.launches) == [(('prep', 1), False), (('warmup', 1), True), (('render', 1), True),
                                            (('prep', 2), False)]

    def test_the_shots_prep_stays_unsampled(self, tmp_path):
        flow, runner, _summary, _job = shots_flow(tmp_path)
        flow.run()
        assert sampled(runner.launches) == [(('prep', 1), False)]


def peak_runner(factory=None):
    """A real ``PhaseRunner``; its fake process runner adds two ticks per sampled launch, a render's in the window."""
    specs = []

    def run(spec, registry):
        specs.append(spec)
        trace = VramTrace(spill=spec.spill)
        if spec.vram_sample is not None:
            for _ in range(2):
                trace.add(spec.vram_sample(1.0), frozenset({JOB_PID}), window=spec.first_output_dir is not None)
        return outcome(vram=trace)

    config = phases.PhaseConfig(cache_dir='D:\\c', zen_port=8668, worker_sid=None, merged_names=[],
                                timeouts={'prep': 1, 'warmup': 2, 'render': 3}, hang_s=900, quit_grace_s=60,
                                log_max_bytes=10, log_flood_bytes_per_min=10)
    r = phases.PhaseRunner(config=config, registry=object(), job_dir=Job(), cancel=lambda: None, shutdown_event=None,
                           sampler=None, run=run,
                           prelaunch=lambda *a, **k: PreLaunch(ok=True, profile_reset=0, user_cvars=0),
                           verify_zen=lambda *a, **k: ZenLogResult(ok=True), loopback_factory=lambda *a, **k: object(),
                           card_key=JOB_KEY, spill=factory)
    return r, specs


def launch_readings(job_mb, other_mb=1000):
    """Pre, two ticks and post of one launch: the job's processes hold ``job_mb`` on its card while it runs."""
    return script((other(other_mb), card(500, 0)), (other(other_mb), card(job_mb + 400, job_mb)),
                  (other(other_mb), card(job_mb // 2 + 400, job_mb // 2)), (other(other_mb), card(510, 0)))


def counted_watches(made):
    """``spill.watch_for``'s factory, counting the watches it makes."""
    factory = spill.watch_for(gate(), 42)

    def make():
        made.append(1)
        return factory()
    return make


class TestSampledWarmUpTrace:

    def test_a_sampled_warm_up_is_read_on_its_card_with_no_spill_watch(self):
        made = []
        r, specs = spill_runner([two_cards(n, 0) for n in range(1, 5)], counted_watches(made))
        run = r.launch('warmup', 1, SimpleNamespace(), sample=True, adapter=CHOICE)
        assert specs[0].vram_sample is not None and specs[0].vram_baseline == {1: 9000}
        assert specs[0].spill is None and run.outcome.vram.spill is None and made == []
        assert run.pre == {1: 9000} and run.post == {1: 9000} and run.outcome.vram.max_job == {1: 8000}

    def test_the_render_after_it_still_gets_its_watch(self):
        made = []
        r, specs = spill_runner([two_cards(n, 0) for n in range(1, 5)], counted_watches(made))
        r.launch('warmup', 1, SimpleNamespace(), sample=True, adapter=CHOICE)
        r.sampler = Sampler([two_cards(n, 0) for n in range(5, 9)])
        r.launch('render', 1, SimpleNamespace(), sample=True, adapter=CHOICE)
        assert specs[0].spill is None and specs[1].spill is not None and made == [1]

    def test_a_cold_warm_up_above_the_render_is_the_jobs_peak(self):
        """Through the real phase runner: the prep is unsampled, the warm-up holds 12,000 MB, the render 7,100."""
        r, specs = peak_runner()
        r.sampler = Sampler(launch_readings(12000) + launch_readings(7100))
        r.launch('prep', 1, SimpleNamespace())
        r.launch('warmup', 1, SimpleNamespace(), sample=True, adapter=CHOICE)
        r.launch('render', 1, SimpleNamespace(), sample=True, adapter=CHOICE)
        assert specs[0].vram_sample is None and r.runs[0].outcome.vram.max_job == {}
        assert [run.outcome.vram.max_job for run in r.runs[1:]] == [{1: 12000}, {1: 7100}]
        assert job_memory.job_peak_mb(r.runs) == 12000
        assert job_memory.summary_values(r.runs)['vram_job_peak_mb'] == 7100   # the render's window figure


class TestJobPeakPhases:

    def test_the_peak_phases_are_the_warm_up_and_the_render(self):
        assert job_memory.PEAK_PHASES == ('warmup', 'render')
        assert 'prep' in job_memory.PHASES and 'prep' not in job_memory.PEAK_PHASES

    def test_a_prep_peak_never_counts(self, tmp_path):
        runs = [render_run(tmp_path, max_job=30000, phase='prep'), render_run(tmp_path, max_job=9000, phase='warmup'),
                render_run(tmp_path, max_job=7000)]
        assert job_memory.job_peak_mb(runs) == 9000
        assert job_memory.job_peak_mb(runs[:1]) is None

    def test_a_retry_warm_up_counts(self, tmp_path):
        runs = [render_run(tmp_path, 1, max_job=8000, phase='warmup'), render_run(tmp_path, 1, max_job=7000),
                render_run(tmp_path, 2, max_job=12000, phase='warmup'), render_run(tmp_path, 2, max_job=7100)]
        assert job_memory.job_peak_mb(runs) == 12000

    def test_a_render_above_the_warm_up_is_still_the_peak(self, tmp_path):
        runs = [render_run(tmp_path, max_job=6000, phase='warmup'), render_run(tmp_path, max_job=7100)]
        assert job_memory.job_peak_mb(runs) == 7100

    def test_a_warm_up_with_no_attributed_sample_adds_nothing(self, tmp_path):
        runs = [render_run(tmp_path, phase='warmup'), render_run(tmp_path, max_job=7100)]
        assert job_memory.job_peak_mb(runs) == 7100


class TestMemoryReport:

    def test_the_body_reports_the_warm_up_peak_and_ignores_the_prep(self, tmp_path):
        runs = [render_run(tmp_path, max_job=30000, phase='prep'), render_run(tmp_path, max_job=12000, phase='warmup'),
                render_run(tmp_path, max_job=7100)]
        assert memory_report.body(job_run(runs))['job_peak_mb'] == 12000

    def test_the_summary_peak_is_still_the_last_render_launchs(self, tmp_path):
        first = render_run(tmp_path, 1, max_job=9800)
        warm = render_run(tmp_path, 2, max_job=12000, phase='warmup')
        last = render_run(tmp_path, 2, max_job=7300)
        first.outcome.vram.job_peak_mb, warm.outcome.vram.job_peak_mb, last.outcome.vram.job_peak_mb = 9500, 11500, 7000
        runs = [first, warm, last]
        assert job_memory.summary_values(runs)['vram_job_peak_mb'] == 7000
        assert memory_report.body(job_run(runs))['job_peak_mb'] == 12000
        assert job_memory.summary_values([first, warm])['vram_job_peak_mb'] == 9500  # a later warm-up never replaces it

    def test_a_prep_peak_alone_does_not_make_a_report_due(self, tmp_path):
        runs = [render_run(tmp_path, max_job=12000, phase='prep')]
        assert job_memory.job_peak_mb(runs) is None
        assert memory_report.due(params(), job_run(runs).runner) is False
