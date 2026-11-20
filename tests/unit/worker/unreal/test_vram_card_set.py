# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The Unreal reading cut to the Unreal cards in use (GPU memory reader D22,
FR-GU-2) and to the one card of a launch (FR-GU-11, ``only_card``).
"""
import logging
from types import SimpleNamespace

import pytest

from sethlans_worker_agent.unreal_executor import vram

KEY_RADEON = 'pci:0000:0a:00.0'
KEY_GEFORCE = 'pci:0000:41:00.0'
ADDR_RADEON = (0, 0x0a, 0, 0)
ADDR_GEFORCE = (0, 0x41, 0, 0)
RADEON = vram.GpuMem(1, 'Radeon Test 24G', 24560, 300, None, None, KEY_RADEON)
GEFORCE = vram.GpuMem(2, 'GeForce Test 16G', 16303, 12000, None, None, KEY_GEFORCE)
KEYLESS = vram.GpuMem(0, 'Arc Test Integrated', 8192, 100, None, None, None)
LIVE = vram.GpuMemoryReading(7.0, 'test-backend', (KEYLESS, RADEON, GEFORCE), measures_card=True)
LOGGER = 'sethlans_worker_agent.unreal_executor.vram'


@pytest.fixture(autouse=True)
def _fresh_set():
    vram.reset_for_tests()
    yield
    vram.reset_for_tests()


def _take(cards):
    return vram.take_reading(0, clock=lambda: 50.0, read=lambda pids: LIVE, pids=frozenset, cards=lambda: cards)


def _found(*keys):
    return tuple(SimpleNamespace(key=key) for key in keys), ''


class Current:
    """``unreal_cards.current()`` stand-in: returns or raises the scripted values in order."""

    def __init__(self, *values):
        self.values, self.calls = list(values), 0

    def __call__(self):
        self.calls += 1
        value = self.values.pop(0) if len(self.values) > 1 else self.values[0]
        if isinstance(value, Exception):
            raise value
        return value


def _set(current, now=100.0, gen=3):
    return vram.unreal_card_set(clock=lambda: now, current=current, generation=lambda: gen)


class TestCut:

    def test_kept_by_address_with_their_index_and_keyless_cards_dropped(self):
        reading = _take(frozenset({ADDR_RADEON}))
        assert reading.gpus == (RADEON,) and reading.gpus[0].index == 1
        assert reading.taken_at == 50.0 and reading.source == 'test-backend' and reading.measures_card is True

    def test_every_unreal_card_is_kept_in_reading_order(self):
        assert _take(frozenset({ADDR_GEFORCE, ADDR_RADEON})).gpus == (RADEON, GEFORCE)

    @pytest.mark.parametrize('cards', [frozenset(), frozenset({(0, 0x99, 0, 0)}), None])
    def test_no_card_left_or_no_decision_gives_none(self, cards):
        assert _take(cards) is None

    def test_the_override_is_not_cut_and_never_asks_for_the_cards(self):
        def cards():
            raise AssertionError('card set read under the override')

        reading = vram.take_reading(20480, clock=lambda: 1.0, read=lambda pids: LIVE, cards=cards)
        assert reading.gpus == (vram.GpuMem(0, '', 20480, 0, None, None),) and reading.source == 'override'

    def test_without_a_cards_argument_the_decided_set_is_used(self, monkeypatch):
        from sethlans_worker_agent.capacity import rebuild
        from sethlans_worker_agent.cards import unreal_cards
        asked = []
        monkeypatch.setattr(unreal_cards, 'current', lambda **kw: (asked.append(kw), _found(KEY_GEFORCE))[1])
        monkeypatch.setattr(rebuild, 'generation', lambda: 1)
        reading = vram.take_reading(0, clock=lambda: 2.0, read=lambda pids: LIVE, pids=frozenset)
        assert reading.gpus == (GEFORCE,)
        assert asked == [{'cached_only': True}]  # the sampler thread never probes (review round)


class TestOnlyCard:

    def test_keeps_the_one_card(self):
        assert vram.only_card(LIVE, KEY_GEFORCE).gpus == (GEFORCE,)

    def test_matches_by_address_integers_not_text(self):
        assert vram.only_card(LIVE, 'pci:00000000:41:00.0').gpus == (GEFORCE,)

    @pytest.mark.parametrize('key', [None, 'pci:0000:99:00.0', 'apple-gpu', 'not-a-key'])
    def test_a_reading_without_the_card_is_no_sample(self, key):
        assert vram.only_card(LIVE, key) is None

    def test_no_reading(self):
        assert vram.only_card(None, KEY_RADEON) is None


class TestCardSetCache:

    def test_decided_set_holds_the_addresses_and_skips_cards_without_one(self):
        current = Current(_found(KEY_RADEON, 'apple-gpu', KEY_GEFORCE))
        assert _set(current) == frozenset({ADDR_RADEON, ADDR_GEFORCE})

    def test_reused_within_five_seconds(self):
        current = Current(_found(KEY_RADEON), _found(KEY_GEFORCE))
        assert _set(current, now=100.0) == {ADDR_RADEON}
        assert _set(current, now=104.9) == {ADDR_RADEON} and current.calls == 1

    @pytest.mark.parametrize('later', [105.0, 160.0, 99.0])
    def test_recomputed_from_five_seconds_on_or_when_the_clock_went_back(self, later):
        current = Current(_found(KEY_RADEON), _found(KEY_GEFORCE))
        _set(current, now=100.0)
        assert _set(current, now=later) == {ADDR_GEFORCE} and current.calls == 2

    def test_recomputed_at_once_after_a_rebuild(self):
        current = Current(_found(KEY_RADEON), _found(KEY_GEFORCE))
        _set(current, now=100.0, gen=3)
        assert _set(current, now=100.1, gen=4) == {ADDR_GEFORCE} and current.calls == 2

    def test_cannot_decide_keeps_the_last_set_and_is_never_cached(self):
        current = Current(_found(KEY_RADEON), None, None, _found(KEY_GEFORCE))
        _set(current, now=100.0)
        assert _set(current, now=110.0) == {ADDR_RADEON}
        assert _set(current, now=110.1) == {ADDR_RADEON} and current.calls == 3
        assert _set(current, now=110.2) == {ADDR_GEFORCE}

    def test_an_empty_decision_is_a_decision(self):
        current = Current(_found(KEY_RADEON), ((), 'no card ticked'))
        _set(current, now=100.0)
        assert _set(current, now=106.0) == frozenset()

    def test_before_any_decision_the_set_is_none(self):
        assert _set(Current(None)) is None
        assert _set(Current(RuntimeError('store unreadable'))) is None
        assert _take(None) is None

    def test_a_failure_keeps_the_last_set_logs_once_and_is_never_cached(self, caplog):
        current = Current(_found(KEY_RADEON), RuntimeError('probe broke'), RuntimeError('probe broke'),
                          _found(KEY_GEFORCE), RuntimeError('probe broke'))
        _set(current, now=100.0)
        caplog.clear()
        with caplog.at_level(logging.ERROR, logger=LOGGER):
            assert _set(current, now=106.0) == {ADDR_RADEON}
            assert _set(current, now=106.1) == {ADDR_RADEON}
            assert len(caplog.records) == 1 and caplog.records[0].exc_info is not None
            assert _set(current, now=106.2) == {ADDR_GEFORCE}   # a success re-arms the log
            assert _set(current, now=112.0) == {ADDR_GEFORCE}
        assert current.calls == 5 and len(caplog.records) == 2

    def test_take_reading_uses_the_last_set_while_the_cards_cannot_be_decided(self):
        current = Current(_found(KEY_RADEON), None)
        _set(current, now=100.0)
        reading = _take(_set(current, now=200.0))
        assert reading.gpus == (RADEON,)

    def test_reset_forgets_the_decided_set(self):
        _set(Current(_found(KEY_RADEON)))
        vram.reset_for_tests()
        assert _set(Current(None)) is None
