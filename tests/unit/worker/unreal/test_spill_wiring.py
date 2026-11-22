# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Where D9's watches come from and where their figures go (Unreal per-machine memory test D9, FR-MT-S-2).

``job_run`` hands the phase runner ``spill.watch_for(gate, job_id)`` (not for a shots job); the phase
runner gives each **sampled render** launch a fresh watch (``RunSpec.spill``); the process runner's trace
feeds it every sample of the launch, before the first output file too. A pilot's ``shared_peak_mb`` is
its largest own shared usage over its last render launch (``spill.shared_peak``), sent in ``pilot-result/``.
"""
import dataclasses
from types import SimpleNamespace

import pytest

from sethlans_worker_agent import config_unreal
from sethlans_worker_agent.unreal_executor import job_run, phases, spill
from sethlans_worker_agent.unreal_executor.delivery import Outcome
from sethlans_worker_agent.unreal_executor.outcomes import JobFailure
from sethlans_worker_agent.unreal_executor.prelaunch import PreLaunch
from sethlans_worker_agent.unreal_executor.runner_types import VramTrace
from sethlans_worker_agent.unreal_executor.vram import GpuMemoryReading
from sethlans_worker_agent.unreal_executor.zen_log import ZenLogResult
from tests.unit.worker.unreal._executor_fakes import machine_result, outcome, params
from tests.unit.worker.unreal._job_run_world import World, pilot_flow as world_pilot_flow, png_flow
from tests.unit.worker.unreal._process_runner_world import World as ProcessWorld
from tests.unit.worker.unreal._runner_fakes import ROOT_PID
from tests.unit.worker.unreal.test_delivery_card_key import KEY, _delivery, _flow as pilot_result_flow
from tests.unit.worker.unreal.test_phases_card_cut import CHOICE, JOB_KEY, JOB_PID, OTHER_KEY, Job, Sampler, card, other
from tests.unit.worker.unreal.test_pilot_flow_failure import LINE, failing_flow, launch

MARGIN = 256
OVER = 1000 + MARGIN + 1


@pytest.fixture(autouse=True)
def _margin(monkeypatch):
    monkeypatch.setattr(config_unreal, 'SPILL_MARGIN_MB', MARGIN)


def gate(shared_peak_mb=1000, used_mb=1000):
    return SimpleNamespace(result=SimpleNamespace(shared_peak_mb=shared_peak_mb), used_mb=used_mb)


def two_cards(n, shared):
    """Both cards of the machine; the job's card (index 1) holds 8,000 MB of the job's 9,000 MB used."""
    return GpuMemoryReading(float(n), 'test-backend', (other(1000), card(9000)), measures_card=True,
                            per_pid={JOB_KEY: {JOB_PID: 8000}, OTHER_KEY: {}},
                            per_pid_shared={JOB_KEY: {JOB_PID: shared}, OTHER_KEY: {JOB_PID: 20000}})


def phase_runner(readings, factory):
    """A ``PhaseRunner`` whose fake process runner feeds the spec's watch like ``ProcessRunner`` does."""
    specs = []

    def run(spec, registry):
        specs.append(spec)
        trace = VramTrace(spill=spec.spill)
        if spec.vram_sample is not None:
            for _ in range(len(readings) - 2):
                trace.add(spec.vram_sample(1.0), frozenset({JOB_PID}))
        return outcome(vram=trace)

    config = phases.PhaseConfig(cache_dir='D:\\c', zen_port=8668, worker_sid=None, merged_names=[],
                                timeouts={'prep': 1, 'warmup': 2, 'render': 3}, hang_s=900, quit_grace_s=60,
                                log_max_bytes=10, log_flood_bytes_per_min=10)
    r = phases.PhaseRunner(config=config, registry=object(), job_dir=Job(), cancel=lambda: None, shutdown_event=None,
                           sampler=Sampler(readings), run=run,
                           prelaunch=lambda *a, **k: PreLaunch(ok=True, profile_reset=0, user_cvars=0),
                           verify_zen=lambda *a, **k: ZenLogResult(ok=True), loopback_factory=lambda *a, **k: object(),
                           card_key=JOB_KEY, spill=factory)
    return r, specs


class TestPhaseRunner:

    def test_a_sampled_render_gets_a_watch_fed_through_the_card_cut(self):
        readings = [two_cards(1, 0), two_cards(2, OVER), two_cards(3, OVER), two_cards(4, 0)]
        r, specs = phase_runner(readings, spill.watch_for(gate(), 42))
        run = r.launch('render', 1, SimpleNamespace(), sample=True, adapter=CHOICE)
        w = run.outcome.vram.spill
        assert w is specs[0].spill and isinstance(w, spill.SpillWatch)
        assert (w.spilled, w.cause, w.shared_peak_mb, w.others_peak_mb) == (True, spill.SELF, OVER, 1000)

    def test_a_reading_without_the_jobs_card_breaks_the_run(self):
        """The card cut turns a reading that lacks the job's card into no reading: a gap, not a sample."""
        lacking = GpuMemoryReading(3.0, 'test-backend', (other(1000),), measures_card=True,
                                   per_pid={OTHER_KEY: {}}, per_pid_shared={OTHER_KEY: {JOB_PID: 20000}})
        readings = [two_cards(1, 0), two_cards(2, OVER), lacking, two_cards(4, OVER), two_cards(5, 0)]
        r, _specs = phase_runner(readings, spill.watch_for(gate(), 42))
        w = r.launch('render', 1, SimpleNamespace(), sample=True, adapter=CHOICE).outcome.vram.spill
        assert (w.samples, w.spilled) == (2, False)

    def test_each_render_launch_gets_a_fresh_watch(self):
        readings = [two_cards(n, OVER) for n in range(1, 9)]
        r, specs = phase_runner(readings[:4], spill.watch_for(gate(), 42))
        r.launch('render', 1, SimpleNamespace(), sample=True, adapter=CHOICE)
        r.sampler = Sampler(readings[4:])
        r.launch('render', 2, SimpleNamespace(), sample=True, adapter=CHOICE)
        assert specs[0].spill is not specs[1].spill

    @pytest.mark.parametrize('phase,sample', [('prep', True), ('warmup', True), ('render', False)])
    def test_no_watch_for_other_phases_or_an_unsampled_render(self, phase, sample):
        made = []
        r, specs = phase_runner([two_cards(n, OVER) for n in range(1, 5)], lambda: made.append(1))
        r.launch(phase, 1, SimpleNamespace(), sample=sample, adapter=CHOICE)
        assert specs[0].spill is None and made == []

    def test_no_factory_no_watch(self):
        r, specs = phase_runner([two_cards(n, OVER) for n in range(1, 5)], None)
        r.launch('render', 1, SimpleNamespace(), sample=True, adapter=CHOICE)
        assert specs[0].spill is None


class TestProcessRunner:

    def test_the_whole_launch_is_watched_start_up_spike_included(self, tmp_path):
        world = ProcessWorld(tmp_path)
        out = tmp_path / 'output'
        out.mkdir()
        w = spill.watch_for(gate(), 42)()

        def sample(timeout_s):
            written = (out / 'f.png').exists()
            return GpuMemoryReading(world.clock.now, 'pdh', (card(9000),), measures_card=True,
                                    per_pid={JOB_KEY: {ROOT_PID: 8000}},
                                    per_pid_shared={JOB_KEY: {ROOT_PID: 10 if written else OVER}})
        world.steps[4] = lambda: (out / 'f.png').write_bytes(b'x')
        world.steps[9] = lambda: setattr(world.root, 'alive', False)
        result = world.run(world.spec(vram_sample=sample, first_output_dir=out, spill=w))
        assert result.vram.spill is w and result.vram.job_peak_mb == 8000
        assert (w.spilled, w.shared_peak_mb) == (True, OVER)


class TestJobRunHandsTheFactory:

    def test_a_render_watches_with_its_results_reference_and_the_gates_used(self, tmp_path, monkeypatch):
        w = World(tmp_path, monkeypatch)
        w.params = params(unreal_machine_results=[machine_result(shared_peak_mb=512)])
        w.flow_action = png_flow
        w.run()
        made = w.runner.spill()
        assert (made.reference_mb, made.used_at_gate_mb, made.margin_mb, made.job_id) == (512, 1000, MARGIN, 42)

    def test_a_pilot_watches_without_a_reference(self, tmp_path, monkeypatch):
        w = World(tmp_path, monkeypatch, kind='pilot')
        w.flow_action = world_pilot_flow
        w.run()
        assert w.runner.spill().reference_mb is None

    def test_a_shots_job_has_no_watch(self, tmp_path, monkeypatch):
        w = World(tmp_path, monkeypatch)
        w.params = params(kind='shots', frame=0)
        monkeypatch.setattr(job_run, 'ShotsFlow', w._flow_factory)
        w.flow_action = lambda job: (_ for _ in ()).throw(JobFailure('stop'))
        w.run()
        assert w.runner.spill is None


def _watched(shared):
    w = spill.SpillWatch(reference_mb=None, used_at_gate_mb=1000, margin_mb=MARGIN)
    for value in shared:
        w.observe(value, 8000, 9000)
    return w


class TestPilotSharedPeak:

    def test_the_last_render_launchs_own_shared_peak(self, tmp_path):
        """The retry is the launch whose log and driver the pilot reports; the cut-short first launch is not."""
        first, warm, retry = launch(tmp_path, 'render', LINE), launch(tmp_path, 'warmup', 9000, attempt=2), \
            launch(tmp_path, 'render', 12000, attempt=2)
        first.outcome.vram.spill, retry.outcome.vram.spill = _watched([2000, 300]), _watched([640, 900])
        warm.outcome.vram.spill = _watched([5000])    # never a warm-up's figure
        result = failing_flow(tmp_path, [first, warm, retry]).run()
        assert result.shared_peak_mb == 900

    def test_without_a_shared_figure_it_is_none(self, tmp_path):
        assert failing_flow(tmp_path, [launch(tmp_path, 'render', LINE)]).run().shared_peak_mb is None

    def test_pilot_result_carries_it(self):
        calls = []
        flow = dataclasses.replace(pilot_result_flow(KEY), shared_peak_mb=640)
        _delivery(calls).deliver(Outcome('success', flow=flow), integrity_ok=True)
        [(name, body)] = calls
        assert name == 'pilot' and body['shared_peak_mb'] == 640


def test_production_delivery_sends_the_memory_report_through_api_extras():
    from sethlans_worker_agent import api_extras
    from sethlans_worker_agent.unreal_executor import executor
    assert executor.default_run_deps().delivery.report_memory is api_extras.report_memory
