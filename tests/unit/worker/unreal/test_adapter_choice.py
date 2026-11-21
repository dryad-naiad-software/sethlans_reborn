# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The Unreal card choice (card selection FR-CU-1, D8, B4; AC-12's ``N``).

``choose()`` re-runs the Windows probe at each call, takes the Unreal cards in
use and picks one per D8; ``N`` is the card's raw index in the DXGI list,
which counts the software and virtual adapters. The fake bindings stand for
the ``EnumAdapterByGpuPreference(HIGH_PERFORMANCE)`` list; that ``win_dxgi``
walks that list, and ``EnumAdapters1`` only without ``IDXGIFactory6``, is
tested in ``gpu_memory/test_win_dxgi.py``.
"""
import pytest

from sethlans_worker_agent.cards import unreal_cards
from sethlans_worker_agent.unreal_executor import adapter_choice
from sethlans_worker_agent.unreal_executor.outcomes import LocalConflict
from tests.unit.worker.unreal._card_machine import CardMachine, adapter, key, software

A03, A23, A79 = '0000:03:00.0', '0000:23:00.0', '0000:79:00.0'
CLAIM = 'c' * 32


class TestIndex:

    def test_index_counts_the_virtual_and_software_adapters(self, monkeypatch):
        CardMachine(monkeypatch, [adapter(0, None), software(1), adapter(2, A23)], ticked=[A23])

        choice = adapter_choice.choose(42, claim_id=CLAIM)

        assert choice == adapter_choice.Choice(42, key(A23), 'Card 2', 2, 0x1002, 0x744C, 0xC8)

    def test_index_is_dxgi_order_not_address_order(self, monkeypatch):
        """M4: a card's index is its place in the DXGI list, never its place in address order."""
        CardMachine(monkeypatch, [adapter(0, A23), software(1), adapter(2, A03)], ticked=[A03])

        assert adapter_choice.choose(42, claim_id=CLAIM).index == 2

    def test_probes_afresh_at_every_launch(self, monkeypatch):
        machine = CardMachine(monkeypatch, [adapter(0, A03), adapter(1, A23, gib=24)], ticked=[A03, A23])
        assert adapter_choice.choose(42, claim_id=CLAIM).index == 1
        machine.pairs = [adapter(0, A23, gib=24), adapter(1, A03)]   # the driver reordered the list
        assert adapter_choice.choose(42, claim_id=CLAIM).index == 0
        assert machine.probes == 2


class TestD8:

    def test_the_only_card(self, monkeypatch):
        CardMachine(monkeypatch, [adapter(0, A03, gib=24), adapter(1, A23, gib=8)], ticked=[A23])
        assert adapter_choice.choose(1, claim_id=CLAIM).key == key(A23)   # an unticked larger card is never taken

    def test_the_largest_dedicated_memory(self, monkeypatch):
        CardMachine(monkeypatch, [adapter(0, A03, gib=16), adapter(1, A23, gib=24), adapter(2, A79, gib=2)],
                    ticked=[A03, A23, A79])
        assert adapter_choice.choose(1, claim_id=CLAIM).key == key(A23)

    def test_a_tie_goes_to_the_lowest_address(self, monkeypatch):
        CardMachine(monkeypatch, [adapter(0, A23, gib=20), adapter(1, A03, gib=20)], ticked=[A23, A03])
        assert adapter_choice.choose(1, claim_id=CLAIM)[:4] == (1, key(A03), 'Card 1', 1)

    @pytest.mark.parametrize('method', ['cpu', 'gpu', 'cpu_gpu'])
    def test_the_compute_method_plays_no_part(self, monkeypatch, method):
        """C-23: the ticks are firm for Unreal whatever the method."""
        CardMachine(monkeypatch, [adapter(0, A03)], ticked=[A03], method=method)
        assert adapter_choice.choose(1, claim_id=CLAIM).index == 0

    def test_a_card_blender_lost_is_still_used(self, monkeypatch):
        """D19: Unreal may still render on a card Blender no longer offers."""
        alert = {'id': 'a' * 32, 'kind': 'card_lost_blender', 'key': key(A23), 'name': 'Card 1', 'detail': ''}
        CardMachine(monkeypatch, [adapter(0, A03), adapter(1, A23, gib=24)], ticked=[A03, A23], alerts=[alert])
        assert adapter_choice.choose(1, claim_id=CLAIM).key == key(A23)

    def test_a_held_card_is_skipped(self, monkeypatch):
        alert = {'id': 'a' * 32, 'kind': 'card_lost', 'key': key(A23), 'name': 'Card 1', 'detail': ''}
        CardMachine(monkeypatch, [adapter(0, A03), adapter(1, A23, gib=24)], ticked=[A03, A23], alerts=[alert])
        assert adapter_choice.choose(1, claim_id=CLAIM).key == key(A03)


class TestOneCardPerJob:
    """D8 (review): later launches keep the card of the job's first launch and only re-resolve its index."""

    def test_the_kept_card_wins_over_a_larger_one(self, monkeypatch):
        machine = CardMachine(monkeypatch, [adapter(0, A03, gib=16), adapter(1, A23, gib=16)], ticked=[A03, A23])
        first = adapter_choice.choose(5, claim_id=CLAIM)
        assert first.key == key(A03)
        machine.pairs = [adapter(0, A23, gib=48), adapter(1, A03, gib=16)]   # reordered, and A23 now larger
        again = adapter_choice.choose(5, key=first.key, claim_id=CLAIM)
        assert (again.key, again.index) == (key(A03), 1)

    def test_the_kept_card_gone_is_a_local_conflict(self, monkeypatch):
        machine = CardMachine(monkeypatch, [adapter(0, A03), adapter(1, A23)], ticked=[A03, A23])
        first = adapter_choice.choose(5, claim_id=CLAIM)
        machine.pairs = [adapter(0, A23)]
        with pytest.raises(LocalConflict) as info:
            adapter_choice.choose(5, key=first.key, claim_id=CLAIM)
        assert info.value.message == adapter_choice.KEPT_CARD_REASON

    def test_the_kept_key_matches_by_address_not_text(self, monkeypatch):
        """Review round: the gate's key spelled with a wider domain names the same card (no release loop)."""
        CardMachine(monkeypatch, [adapter(0, A03), adapter(1, A23, gib=24)], ticked=[A03, A23])
        assert adapter_choice.choose(5, key='pci:00000000:03:00.0', claim_id=CLAIM).key == key(A03)

    @pytest.mark.parametrize('kept', ['pci:bogus', 'apple-gpu', ''])
    def test_an_unreadable_kept_key_matches_nothing(self, monkeypatch, kept):
        CardMachine(monkeypatch, [adapter(0, A03)], ticked=[A03])
        with pytest.raises(LocalConflict) as info:
            adapter_choice.choose(5, key=kept, claim_id=CLAIM)
        assert info.value.message == adapter_choice.KEPT_CARD_REASON


class TestNoCard:
    """FR-CU-1: ``LocalConflict('local_conflict', <FR-CU-6 text>)`` before any launch."""

    def _refused(self, job_id=7):
        with pytest.raises(LocalConflict) as info:
            adapter_choice.choose(job_id, claim_id=CLAIM)
        assert info.value.reason == 'local_conflict'
        assert adapter_choice.all_current() == ()
        return info.value.message

    def test_no_ticked_card_probes_nothing(self, monkeypatch):
        machine = CardMachine(monkeypatch, [adapter(0, A03)], ticked=[])
        assert self._refused() == unreal_cards.NO_TICK_REASON
        assert machine.probes == 0

    def test_the_ticked_card_is_lost(self, monkeypatch):
        CardMachine(monkeypatch, [adapter(0, A03)], ticked=[A23])
        assert self._refused() == unreal_cards.NONE_USABLE_REASON

    def test_the_ticked_card_changed_model(self, monkeypatch):
        machine = CardMachine(monkeypatch, [adapter(0, A03)], ticked=[A03])
        machine.pairs = [adapter(0, A03, device=0x73BF)]
        assert self._refused() == unreal_cards.NONE_USABLE_REASON

    def test_the_probe_failed(self, monkeypatch):
        machine = CardMachine(monkeypatch, [adapter(0, A03)], ticked=[A03])
        machine.dxgi_ok = False
        assert self._refused() == unreal_cards.PROBE_FAILED_REASON

    def test_the_saved_choice_cannot_be_read(self, monkeypatch):
        machine = CardMachine(monkeypatch, [adapter(0, A03)], ticked=[A03])
        machine.read_failed = True
        assert self._refused() == adapter_choice.READ_FAILED_REASON
        assert machine.probes == 0


class TestCurrent:
    """C-24 m: each running Unreal claim's card, for the card check (render per card D16, FR-PC-SEL-3)."""

    def test_the_latest_choice_is_kept_until_the_claim_ends(self, monkeypatch):
        CardMachine(monkeypatch, [adapter(0, A03)], ticked=[A03])
        choice = adapter_choice.choose(9, claim_id=CLAIM)
        assert adapter_choice.all_current() == (choice,)
        adapter_choice.forget('d' * 32)   # another claim's end does not clear it
        assert adapter_choice.all_current() == (choice,)
        adapter_choice.forget(CLAIM)
        assert adapter_choice.all_current() == ()

    def test_the_map_is_keyed_by_claim_not_job(self, monkeypatch):
        """D16: a retained claim and a re-claim of the same job keep one entry each; forgetting one keeps the other."""
        CardMachine(monkeypatch, [adapter(0, A03), adapter(1, A23)], ticked=[A03, A23])
        retained = adapter_choice.choose(9, key=key(A03), claim_id='a' * 32)
        again = adapter_choice.choose(9, key=key(A23), claim_id='b' * 32)
        assert set(adapter_choice.all_current()) == {retained, again}
        adapter_choice.forget('b' * 32)
        assert adapter_choice.all_current() == (retained,)

    def test_a_later_launch_of_one_claim_replaces_its_choice(self, monkeypatch):
        CardMachine(monkeypatch, [adapter(0, A03), adapter(1, A23)], ticked=[A03, A23])
        adapter_choice.choose(9, key=key(A03), claim_id=CLAIM)
        adapter_choice.choose(9, key=key(A03), claim_id=CLAIM)
        assert len(adapter_choice.all_current()) == 1

    @pytest.mark.parametrize('claim', ['', None])
    def test_a_choice_without_a_claim_id_is_refused(self, monkeypatch, claim):
        CardMachine(monkeypatch, [adapter(0, A03)], ticked=[A03])
        with pytest.raises(ValueError):
            adapter_choice.choose(9, claim_id=claim)
        assert adapter_choice.all_current() == ()

    def test_reaches_the_card_check_through_the_bridge(self, monkeypatch):
        from sethlans_worker_agent import unreal_bridge
        CardMachine(monkeypatch, [adapter(0, A03), adapter(1, A23)], ticked=[A03, A23])
        assert unreal_bridge.unreal_cards_in_use() == ()
        adapter_choice.choose(9, key=key(A23), claim_id='a' * 32)
        adapter_choice.choose(4, key=key(A03), claim_id='b' * 32)
        assert unreal_bridge.unreal_cards_in_use() == ((4, key(A03)), (9, key(A23)))
