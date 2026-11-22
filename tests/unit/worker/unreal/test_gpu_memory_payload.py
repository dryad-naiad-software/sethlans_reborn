# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Heartbeat ``gpu_memory``, the pilot gate and the pilot's summary values (GPU memory reader FR-GU-3, FR-GU-7, FR-GU-9).

Chunk 2: the pilot-result body does not carry ``card_key`` yet (the manager's strict schema gets it in chunk 1).
"""
import re

import pytest

from sethlans_worker_agent.gpu_memory import nvidia_smi
from sethlans_worker_agent.unreal_executor import pilot, vram, vram_gate_check
from sethlans_worker_agent.unreal_executor.runner_types import VramTrace
from sethlans_worker_agent.unreal_executor.vram import GpuMem, GpuMemoryReading
from tests.unit.worker.unreal._executor_fakes import KIND, params, phase_run, trace

CARD_KEY = re.compile(r'pci:[0-9a-f]{4,8}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]')


def two_cards(measures_process=True):
    return GpuMemoryReading(10.0, 'fake-a', (
        GpuMem(0, 'Test Card A', 16384, 900, 100, 800, 'pci:0000:0a:00.0'),
        GpuMem(1, 'Test Card B', 8192, 300, 0, 300, 'pci:0001:0b:00.1')), True, measures_process)


class TestHeartbeatPayload:

    def test_capabilities_and_card_keys(self):
        payload = vram.to_payload(two_cards(), now=12.0)
        assert payload['schema'] == 1 and payload['capabilities'] == {'card': True, 'process': True}
        assert [gpu['card_key'] for gpu in payload['gpus']] == ['pci:0000:0a:00.0', 'pci:0001:0b:00.1']
        assert all(CARD_KEY.fullmatch(gpu['card_key']) for gpu in payload['gpus'])

    @pytest.mark.parametrize('card,process', [(True, False), (False, False), (False, True)])
    def test_capabilities_follow_the_reading(self, card, process):
        reading = GpuMemoryReading(1.0, 'fake-a', (GpuMem(0, 'Test Card A', 1024, 0, None, None),), card, process)
        assert vram.to_payload(reading, now=1.0)['capabilities'] == {'card': card, 'process': process}

    def test_an_unknown_card_key_is_null(self):
        reading = GpuMemoryReading(1.0, 'fake-a', (GpuMem(0, 'Test Card A', 1024, 0, None, None),), True)
        assert vram.to_payload(reading, now=1.0)['gpus'][0]['card_key'] is None

    def test_a_bus_id_from_the_backend_reaches_the_payload_as_a_card_key(self, monkeypatch):
        monkeypatch.setattr(nvidia_smi, '_is_windows', lambda: True)  # no attribution queries
        out = "3, 0000000A:4B:00.0, 12288, 2048, Test Card A\n"
        backend = nvidia_smi.NvidiaSmiBackend(run=lambda args: out, clock=lambda: 2.0)
        reading = vram.take_reading(0, clock=lambda: 2.0, read=lambda pids: backend.sample(pids()),
                                    pids=lambda: frozenset(), cards=lambda: frozenset({(0x0a, 0x4b, 0, 0)}))
        [gpu] = vram.to_payload(reading, now=2.0)['gpus']
        assert gpu['card_key'] == 'pci:000a:4b:00.0' and gpu['index'] == 3

    def test_the_override_measures_nothing(self):
        payload = vram.to_payload(vram.take_reading(4096, clock=lambda: 1.0), now=1.0)
        assert payload['source'] == 'override' and payload['capabilities'] == {'card': False, 'process': False}
        assert payload['gpus'] == [{'index': 0, 'name': '', 'total_mb': 4096, 'used_mb': 0, 'sethlans_used_mb': None,
                                    'other_used_mb': None, 'card_key': None}]


class TestPilotGate:

    def test_a_reading_that_does_not_measure_the_card_fails_whatever_its_source(self):
        reading = GpuMemoryReading(1.0, 'nvidia-smi', (GpuMem(0, 'Test Card A', 32768, 0, None, None),))
        result = vram_gate_check.evaluate(params(kind='pilot'), reading)
        assert not result.ok and result.message == "a pilot needs a live GPU memory reading (source nvidia-smi)."

    def test_any_source_that_measures_the_card_passes(self):
        reading = GpuMemoryReading(1.0, 'fake-a', (GpuMem(0, 'Test Card A', 32768, 0, None, None, card_kind=KIND),),
                                   True)
        result = vram_gate_check.evaluate(params(kind='pilot'), reading)
        assert result.ok and result.summary['vram_source'] == 'fake-a'

    def test_the_measured_gate_refuses_the_override(self):
        """Per-machine memory test D14: the override's one GPU has no card kind, so no result applies to it."""
        result = vram_gate_check.evaluate(params(kind='render', peak=4000), vram.take_reading(32768))
        assert not result.ok and result.message == vram_gate_check.NO_RESULT_CARD


def _sample(used, ours, taken_at, index=0):
    return GpuMemoryReading(taken_at, 'fake-a', (GpuMem(index, 'Test Card A', 16384, used, ours,
                                                        None if ours is None else max(0, used - ours)),), True)


class TestProcessPeak:

    def test_the_trace_keeps_the_largest_attributed_value_per_card(self):
        t = VramTrace()
        for taken_at, (used, ours) in enumerate([(1000, 200), (3000, 1800), (2500, None), (2000, 900)]):
            t.add(_sample(used, ours, float(taken_at)))
        assert t.max_sethlans == {0: 1800}

    def test_a_trace_without_attribution_has_none(self):
        t = VramTrace()
        t.add(_sample(1000, None, 1.0))
        assert t.max_sethlans == {}

    def _runs(self, sethlans_by_phase):
        runs = []
        for phase, ours in sethlans_by_phase:
            t = trace({0: 9000, 1: 2000}, {0: 1000, 1: 1000}, total=16384)
            t.max_job.update(ours)  # the job's own members (FR-PC-ATT-2)
            runs.append(phase_run(phase, {0: 1000, 1: 1000}, {0: 1000, 1: 1000}, t))
        return runs

    def test_the_peak_cards_largest_value_over_every_phase(self):
        metrics = pilot.measure(self._runs([('warmup', {0: 6000, 1: 9999}), ('render', {0: 7100})]), None)
        assert metrics.gpu_index == 0 and metrics.process_peak_mb == 7100  # card 1's 9999 is not the peak card

    def test_none_when_no_sample_had_attribution(self):
        assert pilot.measure(self._runs([('render', {})]), None).process_peak_mb is None


class TestPilotReport:

    def _metrics(self, ours):
        t = trace({0: 9000}, {0: 1000}, total=16384)
        t.max_job.update(ours)
        return pilot.measure([phase_run('render', {0: 1000}, {0: 1000}, t)], None)

    @pytest.mark.parametrize('ours,expected', [({0: 7000}, 7000), ({}, '')])
    def test_summary_carries_pilot_process_peak_mb(self, ours, expected):
        values = pilot.summary_values(self._metrics(ours), [1, 2], 'Test Card A', 'c' * 64)
        assert values['pilot_process_peak_mb'] == expected

    def test_the_pilot_result_body_has_the_card_key_but_no_process_peak(self):
        gpu = GpuMem(0, 'Test Card A', 16384, 1000, None, None, 'pci:0000:0a:00.0')
        body = pilot.report_body(self._metrics({0: 7000}), gpu, prep_s=1, warmup_s=1, render_s=1, frames=1,
                                 engine_build='5.8.4-58902052', overrides_sha256='c' * 64, summary='s',
                                 card_kind=KIND, driver_version=None)
        assert body['gpu'] == {'index': 0, 'name': 'Test Card A', 'total_mb': 16384, 'card_key': 'pci:0000:0a:00.0'}
        assert 'process_peak_mb' not in body
        assert not any('process_peak' in key for key in body)
