# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The per-card hold in the worker's loop (render per card FR-PC-CAP-3, -5, -6, FR-PC-SEL-4 Blender loop, D15).

- The poll asks for Unreal jobs while an Unreal card is free, a Blender GPU
  job running or not; not while none is free, a Blender CPU job runs or a
  rebuild is pending; the polling gate follows the same rule.
- The claim holds the reservation's card; the active entry has no
  ``exclusive`` any more.
- A rebuild waits while an Unreal job holds a card the new profile drops,
  swaps when it keeps it, and no longer waits for Unreal jobs as such.
- The Blender card-stop loop never stops an Unreal job (D19).
"""
import dataclasses

import pytest

from sethlans_worker_agent import job_claim, job_processor, unreal_bridge
from sethlans_worker_agent.capacity import card_check, rebuild
from sethlans_worker_agent.cards import matching, unreal_cards
from sethlans_worker_agent.executors import RendererReservation
from sethlans_worker_agent.job_selection import Selection
from sethlans_worker_agent.unreal_executor import capabilities

from ..executors._fakes import install_capacity, key, unreal_job

UNREAL_ONLY = 'pci:0000:23:00.0'


@pytest.fixture
def unreal_keys(monkeypatch):
    """The Unreal cards in use, as the cached card probe would give them."""
    keys = [key(0), key(1)]
    asked = []

    def current_keys(cached_only=True):
        asked.append(cached_only)
        return tuple(keys)

    monkeypatch.setattr(unreal_cards, 'current_keys', current_keys)
    return keys, asked


@pytest.fixture
def poll_spy(monkeypatch):
    seen = []
    monkeypatch.setattr(unreal_bridge, 'poll_params', lambda can_take: seen.append(can_take) or {})
    return seen


class TestPollRules:

    def test_asked_beside_a_blender_gpu_job_while_a_card_is_free(self, unreal_keys, poll_spy):
        cap = install_capacity(gpu_slot_count=2, cpu_slot_count=1)
        cap.reserve_for_job(10, 'GPU')
        job_processor._unreal_poll_params()
        assert poll_spy == [True] and unreal_keys[1] == [True]  # the cached probe only (no probe on the poll path)

    def test_not_asked_when_every_unreal_card_is_held(self, unreal_keys, poll_spy):
        cap = install_capacity(gpu_slot_count=2, cpu_slot_count=0)
        cap.reserve_for_job(10, 'GPU')
        cap.reserve_unreal_card(1, key(1))
        job_processor._unreal_poll_params()
        assert poll_spy == [False]

    def test_not_asked_beside_a_blender_cpu_job_or_a_pending_rebuild(self, unreal_keys, poll_spy):
        cap = install_capacity(gpu_slot_count=2, cpu_slot_count=1)
        cap.reserve_for_job(10, 'CPU')
        job_processor._unreal_poll_params()
        cap.release_slot(10)
        rebuild.request_rebuild()
        job_processor._unreal_poll_params()
        assert poll_spy == [False, False]

    def test_not_asked_without_a_capacity_or_an_unreal_card(self, unreal_keys, poll_spy):
        job_processor._unreal_poll_params()
        install_capacity(gpu_slot_count=2, cpu_slot_count=1)
        unreal_keys[0].clear()
        job_processor._unreal_poll_params()
        assert poll_spy == [False, False]

    def test_capabilities_ask_nothing_when_unreal_cannot_take_a_job(self):
        assert capabilities.poll_params(can_take=False) == {}

    def test_the_polling_gate_stays_open_while_an_unreal_card_is_free(self, unreal_keys):
        cap = install_capacity(gpu_slot_count=1, cpu_slot_count=0)
        unreal_keys[0][:] = [key(0), UNREAL_ONLY]
        cap.reserve_for_job(10, 'GPU')
        assert cap.is_full() and not job_processor.capacity_is_full()
        cap.reserve_unreal_card(1, UNREAL_ONLY)
        assert job_processor.capacity_is_full()

    def test_a_broken_card_lookup_asks_for_nothing(self, monkeypatch):
        install_capacity(gpu_slot_count=1, cpu_slot_count=0)
        monkeypatch.setattr(unreal_cards, 'current_keys', lambda cached_only=True: 1 / 0)
        assert unreal_bridge.can_take_unreal() is False


class TestClaim:

    def test_the_claim_holds_the_reservations_card(self, monkeypatch):
        from sethlans_worker_agent import api_handler
        cap = install_capacity(gpu_slot_count=2, cpu_slot_count=1)
        monkeypatch.setattr(api_handler, 'claim_job', lambda job_id, worker_id: True)
        reservation = RendererReservation()
        reservation.card_key = key(1)
        job = job_claim.claim_selected(Selection(unreal_job(201), reservation, 'unreal_card'), cap, 1)
        assert cap.holds_snapshot() == {key(1): (201, 'UNREAL')}
        assert job['_reservation'].card_keys == (key(1),)
        entry = job_processor.get_active_jobs_snapshot()[201]
        assert entry['gpu_cards'] == key(1) and 'exclusive' not in entry

    def test_a_held_card_refuses_the_claim_and_releases_the_reservation(self, monkeypatch):
        cap = install_capacity(gpu_slot_count=2, cpu_slot_count=0)
        cap.reserve_unreal_card(1, key(1))
        released = []
        reservation = RendererReservation(on_release=lambda: released.append(True))
        reservation.card_key = 'pci:00000000:02:00.0'  # key(1) spelled with a longer domain
        assert job_claim.claim_selected(Selection(unreal_job(202), reservation, 'unreal_card'), cap, 1) is None
        assert released == [True] and cap.holds_snapshot() == {key(1): (1, 'UNREAL')}


def _without(prof, index):
    cards = tuple(card for card in prof.cards if card.key != key(index))
    return dataclasses.replace(prof, cards=cards, gpu_slot_count=len(cards))


class TestRebuild:

    def test_an_unreal_job_on_a_card_the_new_profile_drops_does_not_block(self):
        """D19: Blender lost the card, Unreal keeps rendering on it; the swap goes ahead and the hold stays."""
        cap = install_capacity(gpu_slot_count=2, cpu_slot_count=1)
        cap.reserve_unreal_card(7, key(1))
        rebuild.request_rebuild()
        assert cap.try_rebuild(_without(cap.profile, 1), rebuild.generation()) == rebuild.SWAPPED
        assert cap.holds_snapshot() == {key(1): (7, 'UNREAL')} and cap.free_keys((key(1),)) == ()

    def test_an_unreal_key_outside_the_old_profile_does_not_block(self):
        """C-27 b: a card ticked for Unreal only was never in the Blender profile."""
        cap = install_capacity(gpu_slot_count=1, cpu_slot_count=1)
        cap.reserve_unreal_card(7, UNREAL_ONLY)
        rebuild.request_rebuild()
        new = _without(cap.profile, 0)
        assert cap.try_rebuild(new, rebuild.generation()) == rebuild.SWAPPED and cap.profile is new

    def test_a_blender_job_on_a_dropped_card_still_blocks(self):
        cap = install_capacity(gpu_slot_count=2, cpu_slot_count=0)
        cap.reserve_unreal_card(7, key(0))
        cap.reserve_for_job(10, 'GPU')  # key(1)
        rebuild.request_rebuild()
        assert cap.try_rebuild(_without(cap.profile, 1), rebuild.generation()) == rebuild.WAITING

    def test_swaps_when_the_unreal_card_stays_and_keeps_the_hold(self):
        cap = install_capacity(gpu_slot_count=2, cpu_slot_count=1)
        cap.reserve_unreal_card(7, key(0))
        rebuild.request_rebuild()
        new = _without(cap.profile, 1)
        assert cap.try_rebuild(new, rebuild.generation()) == rebuild.SWAPPED
        assert cap.profile is new and cap.holds_snapshot() == {key(0): (7, 'UNREAL')}

    def test_a_retained_unreal_key_on_a_removed_card_does_not_block(self):
        cap = install_capacity(gpu_slot_count=2, cpu_slot_count=1)
        cap.reserve_unreal_card(7, key(1))
        cap.retain_slots(7)
        rebuild.request_rebuild()
        assert cap.try_rebuild(_without(cap.profile, 1), rebuild.generation()) == rebuild.SWAPPED

    def test_step_builds_the_profile_while_an_unreal_job_runs(self, monkeypatch):
        """D15: the rebuild no longer waits for Unreal jobs to end; it uses the OS probe only."""
        cap = install_capacity(gpu_slot_count=2, cpu_slot_count=1)
        cap.reserve_unreal_card(7, key(0))
        new = _without(cap.profile, 1)
        monkeypatch.setattr(card_check, 'rebuild_profile', lambda capacity: new)
        rebuild.request_rebuild()
        assert rebuild.step(cap) == rebuild.SWAPPED and cap.profile is new


class TestCardStopsAndDrift:

    @pytest.mark.parametrize('state', [matching.BLENDER_LOST, matching.LOST, matching.HELD])
    def test_the_blender_loop_never_stops_an_unreal_job(self, monkeypatch, state):
        cap = install_capacity(gpu_slot_count=2, cpu_slot_count=0)
        cap.reserve_unreal_card(7, key(0))
        cap.reserve_for_job(10, 'GPU')
        monkeypatch.setattr(unreal_bridge, 'unreal_cards_in_use', lambda: ())  # before its first launch
        assert cap.jobs_on_unusable_cards({key(0): state, key(1): matching.BLENDER_LOST}) == [10]

    def test_the_unreal_loop_stops_it_for_its_own_card(self, monkeypatch):
        cap = install_capacity(gpu_slot_count=2, cpu_slot_count=0)
        cap.reserve_unreal_card(7, key(0))
        monkeypatch.setattr(unreal_bridge, 'unreal_cards_in_use', lambda: ((7, key(0)),))
        assert cap.jobs_on_unusable_cards({key(0): matching.LOST}) == [7]
        assert cap.jobs_on_unusable_cards({key(0): matching.BLENDER_LOST}) == []

    def test_no_blender_detection_while_any_unreal_job_runs(self):
        cap = install_capacity(gpu_slot_count=2, cpu_slot_count=0)
        cap.reserve_for_job(10, 'GPU')
        assert unreal_bridge.drift_check_allowed() is True
        cap.reserve_unreal_card(7, key(1))
        assert unreal_bridge.drift_check_allowed() is False
        cap.release_slot(7)
        assert unreal_bridge.drift_check_allowed() is True


def test_current_keys_are_the_current_cards_keys_from_the_cached_probe(monkeypatch):
    from types import SimpleNamespace
    asked = []
    cards = (SimpleNamespace(key=key(0)), SimpleNamespace(key=UNREAL_ONLY))
    monkeypatch.setattr(unreal_cards, 'current', lambda cached_only=False: asked.append(cached_only) or (cards, ''))
    assert unreal_cards.current_keys() == (key(0), UNREAL_ONLY) and asked == [True]
    monkeypatch.setattr(unreal_cards, 'current', lambda cached_only=False: None)  # undecided
    assert unreal_cards.current_keys() == ()
