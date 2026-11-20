# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The Windows ``pdh`` backend when adapter LUIDs change (GPU memory reader FR-GW-8), with fakes on every OS.

A driver restart gives a card a new LUID; DXGI may list it before PDH has a counter instance for it.
"""
import logging

from tests.unit.worker.gpu_memory._win_fakes import (
    LUID_A, LUID_B, OURS, Clock, FakeCards, FakeQuery, adapter_item, collect, make_backend, two_cards)

LOG = 'sethlans_worker_agent.gpu_memory.win_backend'
CHANGED = "GPU adapters changed; the reading restarts."
IDLE = collect([adapter_item(LUID_A, 500), adapter_item(LUID_B, 100)], [])
NEW_LUID = (0, 0x0009F00D)
B_MOVED = collect([adapter_item(LUID_A, 500), adapter_item(NEW_LUID, 100)], [])


def _b_moved_cards():
    return (two_cards()[0], (two_cards()[1][0], NEW_LUID))


def _open(collects, cards, clock):
    backend = make_backend(FakeQuery(collects), cards=cards, clock=clock)
    assert backend.open() is True
    return backend


def _keys(reading):
    return [gpu.card_key for gpu in reading.gpus]


class TestLuidChanges:

    def test_a_changed_luid_lists_the_cards_again_once_then_reads_the_new_map(self, caplog):
        caplog.set_level(logging.INFO, logger=LOG)
        cards, clock = FakeCards(two_cards(), _b_moved_cards()), Clock()
        backend = _open([IDLE, B_MOVED], cards, clock)
        clock.now += 60
        assert backend.sample(OURS) is None
        assert cards.calls == 2 and caplog.messages.count(CHANGED) == 1
        assert [gpu.used_mb for gpu in backend.sample(OURS).gpus] == [500, 100]
        assert cards.calls == 2

    def test_no_new_listing_within_60_s(self):
        cards, clock = FakeCards(), Clock()
        backend = _open([IDLE, B_MOVED], cards, clock)
        clock.now += 59
        assert backend.sample(OURS) is None   # card B has no figure: partial
        assert cards.calls == 1

    def test_the_same_assignment_after_a_new_listing_is_no_change(self, caplog):
        caplog.set_level(logging.WARNING, logger=LOG)
        cards, clock = FakeCards(), Clock()
        extra = collect([adapter_item(LUID_A, 500), adapter_item(LUID_B, 100), adapter_item((0, 0x77), 1)], [])
        backend = _open([IDLE, extra], cards, clock)
        clock.now += 60
        assert [gpu.used_mb for gpu in backend.sample(OURS).gpus] == [500, 100]
        clock.now += 60
        backend.sample(OURS)
        assert cards.calls == 2 and caplog.messages == []   # the extra LUID is known after the second listing

    def test_a_failed_listing_keeps_the_old_map(self):
        cards, clock = FakeCards(two_cards(), None), Clock()
        backend = _open([IDLE, B_MOVED], cards, clock)
        clock.now += 60
        assert backend.sample(OURS) is None and cards.calls == 2

    def test_an_empty_listing_is_no_change(self, caplog):
        caplog.set_level(logging.WARNING, logger=LOG)
        cards, clock = FakeCards(two_cards(), (), _b_moved_cards()), Clock()
        backend = _open([IDLE, B_MOVED], cards, clock)
        clock.now += 60
        assert backend.sample(OURS) is None and caplog.messages == []
        clock.now += 60
        assert backend.sample(OURS) is None and caplog.messages == [CHANGED]
        assert [gpu.used_mb for gpu in backend.sample(OURS).gpus] == [500, 100]


class TestDriverRestart:

    def test_dxgi_lists_the_new_luid_before_pdh_has_its_counter(self, caplog):
        caplog.set_level(logging.INFO, logger=LOG)
        only_a = collect([adapter_item(LUID_A, 500)], [])
        cards, clock = FakeCards(two_cards(), _b_moved_cards()), Clock()
        backend = _open([IDLE, only_a, B_MOVED], cards, clock)
        clock.now += 60
        assert backend.sample(OURS) is None                     # B's counter is gone; DXGI already has its new LUID
        assert caplog.messages.count(CHANGED) == 1
        assert "Card pci:0000:0b:00.0 'Test Card B' has no GPU Adapter Memory counter; it is left out of the " \
               "GPU memory reading." in caplog.messages
        clock.now += 1
        assert _keys(backend.sample(OURS)) == ['pci:0000:0a:00.0']   # B's counter is back; no listing yet (60 s)
        clock.now += 60
        reading = backend.sample(OURS)                          # listed again: same assignment, B now has a counter
        assert _keys(reading) == ['pci:0000:0a:00.0', 'pci:0000:0b:00.0'] and reading.gpus[1].used_mb == 100
        assert cards.calls == 3 and caplog.messages.count(CHANGED) == 1

    def test_pdh_shows_the_new_luid_before_dxgi_lists_it(self, caplog):
        caplog.set_level(logging.WARNING, logger=LOG)
        cards, clock = FakeCards(two_cards(), two_cards(), _b_moved_cards()), Clock()
        backend = _open([IDLE, B_MOVED], cards, clock)
        clock.now += 60
        assert _keys(backend.sample(OURS)) == ['pci:0000:0a:00.0']   # DXGI still has B's old LUID: B left out
        assert cards.calls == 2 and caplog.messages == []
        clock.now += 59
        assert _keys(backend.sample(OURS)) == ['pci:0000:0a:00.0']   # the new LUID is still new, but < 60 s
        assert cards.calls == 2
        clock.now += 1
        assert backend.sample(OURS) is None                         # DXGI now lists the new LUID: restart
        assert cards.calls == 3 and caplog.messages == [CHANGED]
        assert _keys(backend.sample(OURS)) == ['pci:0000:0a:00.0', 'pci:0000:0b:00.0']
        clock.now += 60
        backend.sample(OURS)
        assert cards.calls == 3                                     # every card has a counter: no more listing

    def test_a_card_without_a_counter_keeps_the_listing_at_most_every_60_s(self):
        cards, clock = FakeCards(), Clock()
        backend = _open([IDLE, B_MOVED], cards, clock)               # DXGI never lists the new LUID
        for step in (60, 30, 30, 59, 1):
            clock.now += step
            backend.sample(OURS)
        assert cards.calls == 4                                     # at open, +60, +120, +180

    def test_a_counter_going_away_with_the_same_assignment_drops_the_card(self):
        only_a = collect([adapter_item(LUID_A, 500)], [])
        cards, clock = FakeCards(), Clock()
        backend = _open([IDLE, only_a], cards, clock)
        clock.now += 60
        assert _keys(backend.sample(OURS)) == ['pci:0000:0a:00.0']
