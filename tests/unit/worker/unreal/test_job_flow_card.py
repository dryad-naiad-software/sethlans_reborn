# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The gated card travels to the job's first launch (GPU memory reader D22,
FR-GU-10, AC-21) and a pilot checks its card by key (FR-GU-4).
"""
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Optional

import pytest

from sethlans_worker_agent.unreal_executor import job_flow
from sethlans_worker_agent.unreal_executor.adapter_choice import Choice
from sethlans_worker_agent.unreal_executor.outcomes import JobFailure, LocalConflict
from sethlans_worker_agent.unreal_executor.pilot import PilotMetrics
from sethlans_worker_agent.unreal_executor.summary import Summary
from sethlans_worker_agent.unreal_executor.vram import GpuMem
from tests.unit.worker.unreal._executor_fakes import KIND, params, reading
from tests.unit.worker.unreal._job_run_world import World, png_flow

IDLE_KEY = 'pci:0000:41:00.0'
BUSY_KEY = 'pci:0000:0a:00.0'
BUSY = GpuMem(0, 'GeForce Test 16G', 16303, 12000, None, None, BUSY_KEY, card_kind=KIND)
IDLE = GpuMem(1, 'Radeon Test 24G', 24560, 300, None, None, IDLE_KEY, card_kind=KIND)


@dataclass(frozen=True)
class Ctx:
    graphics_adapter: Optional[int] = None


class Chooser:
    """``adapter_choice.choose`` stand-in: records the key asked for, answers with ``answer``."""

    def __init__(self, answer):
        self.answer, self.keys = answer, []

    def __call__(self, job_id, key=None):
        self.keys.append(key)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def _flow(chooser, first_key, kind='render', gate_reading=None, tmp_path=None):
    job = SimpleNamespace(output=tmp_path, logs=tmp_path)
    return job_flow.UeFlow(params(kind=kind), Ctx(), job, None, Summary(), None, None, 'Demo.uproject',
                           SimpleNamespace(profile=None, log_max_bytes=1024), reading=gate_reading, choose=chooser,
                           first_key=first_key)


class TestFirstLaunchKey:

    def test_the_first_choose_gets_the_gated_key_and_later_ones_the_choices_key(self):
        """The choice's key text differs from the gate's (padding): later launches ask for the choice's."""
        chooser = Chooser(Choice(42, 'pci:00000000:41:00.0', 'Radeon Test 24G', 3))
        flow = _flow(chooser, IDLE_KEY)
        assert flow._adapter_ctx() == Ctx(graphics_adapter=3)
        flow._adapter_ctx()
        flow._adapter_ctx()
        assert chooser.keys == [IDLE_KEY, 'pci:00000000:41:00.0', 'pci:00000000:41:00.0']

    def test_the_override_asks_for_no_card(self):
        chooser = Chooser(Choice(42, IDLE_KEY, 'Radeon Test 24G', 0))
        _flow(chooser, None)._adapter_ctx()
        assert chooser.keys == [None]

    def test_a_gated_card_that_cannot_be_used_raises_the_local_conflict(self):
        flow = _flow(Chooser(LocalConflict('local_conflict', 'the card is gone')), IDLE_KEY)
        with pytest.raises(LocalConflict, match='the card is gone'):
            flow._adapter_ctx()
        assert flow.choice is None


METRICS = PilotMetrics(peak_mb=8102, peak_source='card', card_delta_peak_mb=8102, csv_local_used_peak_mb=None,
                       baseline_used_mb=300, baseline_drift_mb=0, peak_phase='render', gpu_index=1,
                       max_sample_gap_s=1.0)


ADAPTER_LINES = ('[t][0]LogD3D12RHI: Found D3D12 adapter 1: Radeon Test 24G (VendorId: 1002, DeviceId: 744c\n'
                 '[t][0]LogD3D12RHI: Chosen D3D12 Adapter Id = 1\n')


def _pilot_flow(monkeypatch, tmp_path, choice_key, log_text=ADAPTER_LINES):
    flow = _flow(Chooser(None), IDLE_KEY, kind='pilot', gate_reading=reading(gpus=(BUSY, IDLE)), tmp_path=tmp_path)
    log = tmp_path / 'render.1.log'
    log.write_text(log_text, encoding='utf-8')
    flow.choice = Choice(42, choice_key, 'Radeon Test 24G', 1)
    flow.prep = lambda: SimpleNamespace(pilot_frames=(0, 50))
    flow._warm_if_needed = lambda prep: True
    flow._render_with_retry = lambda prep: (SimpleNamespace(log_path=log), SimpleNamespace(scan=None), frozenset())
    flow._render_summary = lambda run, check: None
    flow.runner = SimpleNamespace(runs=[])
    monkeypatch.setattr(job_flow, 'render_check_time', lambda check, render_s: 60)
    monkeypatch.setattr(job_flow.output, 'collect_pilot', lambda *args: [])
    monkeypatch.setattr(job_flow.pilot_csv, 'collect', lambda *args: None)
    monkeypatch.setattr(job_flow.pilot, 'measure', lambda runs, csv_peak: METRICS)
    return flow


class TestPilotCard:

    def test_the_result_carries_the_readings_card_found_by_key(self, monkeypatch, tmp_path):
        result = _pilot_flow(monkeypatch, tmp_path, 'pci:00000000:41:00.0').run()
        assert result.card is IDLE and result.adapter == 'Radeon Test 24G' and result.metrics is METRICS

    def test_a_render_log_without_the_chosen_line_releases_the_pilot(self, monkeypatch, tmp_path):
        """Review round: a silenced LogD3D12RHI could hide another card; release, never measure or blame."""
        flow = _pilot_flow(monkeypatch, tmp_path, IDLE_KEY, log_text=ADAPTER_LINES.splitlines()[0] + '\n')
        with pytest.raises(LocalConflict) as info:
            flow.run()
        assert info.value.reason == 'local_conflict' and info.value.message == job_flow.NO_ADAPTER_LINE_MESSAGE
        assert 'project' not in info.value.message and 'nvidia' not in info.value.message.lower()

    @pytest.mark.parametrize('phase,chosen,released', [('warmup', None, True), ('render', None, True),
                                                       ('prep', None, False), ('warmup', 1, False)])
    def test_every_warm_up_and_render_run_needs_the_chosen_line(self, phase, chosen, released):
        runs = [SimpleNamespace(phase=phase, outcome=SimpleNamespace(adapter_chosen=chosen))]
        if released:
            with pytest.raises(LocalConflict):
                job_flow.require_adapter_lines(runs, 'Radeon Test 24G')
        else:
            job_flow.require_adapter_lines(runs, 'Radeon Test 24G')

    def test_a_card_not_in_the_gate_reading_fails_the_pilot(self, monkeypatch, tmp_path):
        flow = _pilot_flow(monkeypatch, tmp_path, 'pci:0000:c1:00.0')
        with pytest.raises(JobFailure) as info:
            flow.run()
        assert str(info.value) == ("Pilot render failed: the card Unreal rendered on (pci:0000:c1:00.0) is not in "
                                   "the GPU memory reading.")


class TestJobRunHandOff:

    def test_ac21_the_flow_and_the_runner_get_the_idle_cards_key(self, tmp_path, monkeypatch):
        w = World(tmp_path, monkeypatch)
        w.reading = reading(gpus=(BUSY, IDLE))
        w.flow_action = png_flow
        assert w.run().success
        assert w.first_key == IDLE_KEY and w.runner.card_key == IDLE_KEY

    def test_the_override_hands_over_the_held_card(self, tmp_path, monkeypatch):
        """Render per card D14: the launch is asked for the card the claim holds (D8's), so the two never differ."""
        w = World(tmp_path, monkeypatch)
        w.reading = reading(source='override')
        w.card_key = IDLE_KEY
        w.flow_action = png_flow
        assert w.run().success
        assert w.first_key == IDLE_KEY and w.runner.card_key is None  # no gated card for the traces
