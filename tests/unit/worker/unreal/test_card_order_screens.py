# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The Unreal card order's tie rule: among equal-size cards, prefer one without
a screen, then the lowest key.

The memory gate (``vram_gate_check.job_card``) and the launch's choice
(``adapter_choice.pick`` / ``choose``) must pick the same card. The root
conftest makes every screen mark unknown, so each test injects its marks.
"""
import dataclasses

import pytest

from sethlans_worker_agent.cards import screens
from sethlans_worker_agent.cards.inventory import Card
from sethlans_worker_agent.unreal_executor import adapter_choice, pilot, vram, vram_gate_check
from sethlans_worker_agent.unreal_executor.vram import GpuMem, GpuMemoryReading
from tests.unit.worker.unreal._card_machine import CardMachine, adapter, key
from tests.unit.worker.unreal._executor_fakes import params, phase_run, trace

LOW, HIGH = 'pci:0000:03:00.0', 'pci:0000:23:00.0'   # two identical cards; LOW drives the display
A03, A23 = '0000:03:00.0', '0000:23:00.0'


@pytest.fixture(autouse=True)
def _fresh_set():
    vram.reset_for_tests()
    yield
    vram.reset_for_tests()


def _card(card_key, mib=20464, screen=None, index=0):
    return Card(key=card_key, name='Radeon Test 20G', dxgi_index=index, dedicated_mib=mib, screen=screen)


def _gpu(card_key, mib=20464, screen=None, index=0):
    return GpuMem(index, 'Radeon Test 20G', mib, 500, None, None, card_key, screen)


def _gate_key(*gpus):
    return vram_gate_check.job_card(GpuMemoryReading(0.0, 'pdh', tuple(gpus), measures_card=True)).card_key


class TestTies:

    def test_two_equal_cards_the_one_without_a_screen_is_gated_and_picked(self):
        assert _gate_key(_gpu(LOW, screen=True), _gpu(HIGH, screen=False, index=1)) == HIGH
        assert adapter_choice.pick((_card(LOW, screen=True), _card(HIGH, screen=False, index=1))).key == HIGH

    def test_the_gate_result_names_the_card_without_a_screen(self):
        gpus = (_gpu(LOW, screen=True), _gpu(HIGH, screen=False, index=1))
        result = vram_gate_check.evaluate(params(peak=7930), GpuMemoryReading(0.0, 'pdh', gpus, measures_card=True))
        assert result.ok and result.card_key == HIGH

    @pytest.mark.parametrize('low,high', [(None, None), (None, False), (False, None), (False, False),
                                          (True, True), (None, True)])
    def test_unknown_or_equal_marks_tie_to_the_lowest_key_as_before(self, low, high):
        assert _gate_key(_gpu(HIGH, screen=high, index=1), _gpu(LOW, screen=low)) == LOW
        assert adapter_choice.pick((_card(HIGH, screen=high, index=1), _card(LOW, screen=low))).key == LOW

    def test_unknown_counts_as_no_screen(self):
        assert _gate_key(_gpu(LOW, screen=True), _gpu(HIGH, screen=None, index=1)) == HIGH
        assert adapter_choice.pick((_card(LOW, screen=True), _card(HIGH, screen=None, index=1))).key == HIGH

    def test_a_larger_card_with_a_screen_still_wins(self):
        assert _gate_key(_gpu(HIGH, 16384, screen=False, index=1), _gpu(LOW, 24560, screen=True)) == LOW
        assert adapter_choice.pick((_card(HIGH, 16384, screen=False, index=1),
                                    _card(LOW, 24560, screen=True))).key == LOW


SIZES_AND_MARKS = [
    ((20464, True), (20464, False)), ((20464, False), (20464, True)), ((20464, None), (20464, None)),
    ((20464, True), (20464, None)), ((24560, True), (16384, False)), ((16384, None), (24560, True)),
    ((20464, True), (20464, True)),
]


class TestGateAndPickAgree:
    """The same cards, as the cached probe's inventory cards and as the reader's reading."""

    @pytest.mark.parametrize('low,high', SIZES_AND_MARKS)
    def test_on_the_same_input(self, monkeypatch, low, high):
        from sethlans_worker_agent.capacity import rebuild
        from sethlans_worker_agent.cards import unreal_cards
        cards = (_card(LOW, low[0], low[1]), _card(HIGH, high[0], high[1], index=1))
        asked = []
        monkeypatch.setattr(unreal_cards, 'current', lambda **kw: (asked.append(kw), (cards, ''))[1])
        monkeypatch.setattr(rebuild, 'generation', lambda: 1)
        live = GpuMemoryReading(0.0, 'pdh', (_gpu(HIGH, high[0], index=1), _gpu(LOW, low[0])), measures_card=True)

        reading = vram.take_reading(0, clock=lambda: 1.0, read=lambda pids: live, pids=frozenset)

        assert vram_gate_check.job_card(reading).card_key == adapter_choice.pick(cards).key
        assert asked == [{'cached_only': True}]   # the sampler thread: the cached probe only

    def test_the_reading_carries_the_marks_and_unknown_stays_none(self, monkeypatch):
        from sethlans_worker_agent.capacity import rebuild
        from sethlans_worker_agent.cards import unreal_cards
        cards = (_card(LOW, screen=True), _card(HIGH, screen=None, index=1))
        monkeypatch.setattr(unreal_cards, 'current', lambda **kw: (cards, ''))
        monkeypatch.setattr(rebuild, 'generation', lambda: 1)
        live = GpuMemoryReading(0.0, 'pdh', (_gpu(LOW), _gpu('pci:00000000:23:00.0', index=1)))
        reading = vram.take_reading(0, clock=lambda: 1.0, read=lambda pids: live, pids=frozenset)
        assert [gpu.screen for gpu in reading.gpus] == [True, None]   # matched by address, not text
        assert vram.screen_marks() == {(0, 3, 0, 0): True}

    def test_no_decision_leaves_every_mark_unknown(self):
        live = GpuMemoryReading(0.0, 'pdh', (_gpu(LOW, screen=True),))
        reading = vram.take_reading(0, clock=lambda: 1.0, read=lambda pids: live, pids=frozenset,
                                    cards=lambda: frozenset({(0, 3, 0, 0)}))
        assert reading.gpus == (dataclasses.replace(_gpu(LOW), screen=None),)

    def test_the_override_reading_has_no_mark(self):
        reading = vram.take_reading(20480, clock=lambda: 1.0)
        assert reading.gpus[0].screen is None


class TestMarksStayLocal:
    """The mark only orders cards on the worker; it is never sent to the manager."""

    @pytest.mark.parametrize('screen', [True, False, None])
    def test_the_heartbeat_payload_has_no_screen_key(self, screen):
        payload = vram.to_payload(GpuMemoryReading(1.0, 'pdh', (_gpu(LOW, screen=screen),), True), now=2.0)
        assert all('screen' not in gpu for gpu in payload['gpus']) and 'screen' not in payload

    @pytest.mark.parametrize('screen', [True, False, None])
    def test_the_pilot_report_gpu_has_no_screen_key(self, screen):
        metrics = pilot.measure([phase_run('render', {0: 1000}, {0: 1000}, trace({0: 9000}, {0: 1000}))], None)
        body = pilot.report_body(metrics, _gpu(LOW, screen=screen), prep_s=1, warmup_s=1, render_s=1, frames=1,
                                 engine_build='5.8.4-58902052', overrides_sha256='c' * 64, summary='s')
        assert 'screen' not in body['gpu'] and not any('screen' in name for name in body)


def _marks(monkeypatch, found):
    calls = []

    def platform_marks(_platform_name, _inventory):
        calls.append(1)
        return found
    monkeypatch.setattr(screens, '_platform_marks', platform_marks)
    return calls


class TestChoose:

    def test_two_equal_cards_without_a_key_render_on_the_one_without_a_screen(self, monkeypatch):
        CardMachine(monkeypatch, [adapter(0, A03, gib=20), adapter(1, A23, gib=20)], ticked=[A03, A23])
        _marks(monkeypatch, {key(A03): (True, True), key(A23): (False, False)})
        assert adapter_choice.choose(1)[:4] == (1, key(A23), 'Card 1', 1)

    def test_unknown_marks_keep_the_lowest_address(self, monkeypatch):
        CardMachine(monkeypatch, [adapter(0, A23, gib=20), adapter(1, A03, gib=20)], ticked=[A23, A03])
        _marks(monkeypatch, None)
        assert adapter_choice.choose(1).key == key(A03)

    def test_a_larger_card_with_a_screen_still_wins(self, monkeypatch):
        CardMachine(monkeypatch, [adapter(0, A03, gib=24), adapter(1, A23, gib=16)], ticked=[A03, A23])
        _marks(monkeypatch, {key(A03): (True, True), key(A23): (False, False)})
        assert adapter_choice.choose(1).key == key(A03)

    def test_with_a_key_no_screen_is_read(self, monkeypatch):
        """The gate chose already; later launches keep that card, so no screen read is needed."""
        CardMachine(monkeypatch, [adapter(0, A03, gib=20), adapter(1, A23, gib=20)], ticked=[A03, A23])
        calls = _marks(monkeypatch, {key(A03): (True, True), key(A23): (False, False)})
        assert adapter_choice.choose(1, key=key(A03)).key == key(A03) and calls == []
