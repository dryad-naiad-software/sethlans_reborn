# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``WorkerCapacity.reserve_exclusive`` / ``has_no_reservations``
(Unreal foundation FR-WX-10, FR-WX-11, FR-WX-13, D1;
FR-T-5 "Exclusive reservation and idle-only eligibility"; AC-21).
"""
from pathlib import Path

import pytest

from sethlans_worker_agent.capacity import WorkerCapacity

from ..executors._fakes import key, profile


def _cap(**kwargs):
    return WorkerCapacity(profile(**kwargs))


class TestModes:

    def test_split_marks_every_gpu_and_cpu(self):
        cap = _cap(gpu_slot_count=3, cpu_slot_count=1)
        slot = cap.reserve_exclusive(50)
        assert slot is not None
        assert cap.gpu_assignments_snapshot() == {key(0): 50, key(1): 50, key(2): 50}
        assert cap._cpu_slot_job_id == 50
        assert slot.device_used == 'GPU' and slot.card_keys == ()

    def test_combined_marks_every_card(self):
        cap = _cap(gpu_slot_count=1, card_count=2, gpu_mode='combined')
        assert cap.reserve_exclusive(51) is not None
        assert cap.gpu_assignments_snapshot() == {key(0): 51, key(1): 51}
        assert cap.is_full() and cap.free_device_prefs() == []

    def test_no_cpu_slot_means_no_cpu_mark(self):
        cap = _cap(gpu_slot_count=2, cpu_slot_count=0)
        assert cap.reserve_exclusive(52) is not None
        assert cap._cpu_slot_job_id is None
        assert cap.is_full() and cap.free_device_prefs() == []

    def test_no_gpu_slot_returns_none(self):
        cap = _cap(gpu_slot_count=0, cpu_slot_count=1)
        assert cap.reserve_exclusive(53) is None
        assert cap.has_no_reservations()

    def test_single_gpu_split_carries_no_blender_card(self):
        slot = _cap(gpu_slot_count=1).reserve_exclusive(54)
        assert slot.card_keys == ()
        assert slot.job_cards == ()


class TestHeldState:

    @pytest.mark.parametrize('gpus,cpus', [(1, 1), (2, 1), (2, 0)])
    def test_full_while_held_and_free_after_release(self, gpus, cpus):
        cap = _cap(gpu_slot_count=gpus, cpu_slot_count=cpus)
        assert cap.has_no_reservations()
        cap.reserve_exclusive(60)
        assert cap.is_full() and cap.free_device_prefs() == []
        assert not cap.has_no_reservations()
        assert cap.has_exclusive_reservation()
        cap.release_slot(60)
        assert cap.has_no_reservations()
        assert not cap.has_exclusive_reservation()
        assert cap.free_device_prefs()

    @pytest.mark.parametrize('pref', ['GPU', 'CPU'])
    def test_any_busy_slot_blocks_exclusive(self, pref):
        cap = _cap(gpu_slot_count=2, cpu_slot_count=1)
        cap.reserve_for_job(70, pref)
        assert not cap.has_no_reservations()
        assert cap.reserve_exclusive(71) is None
        assert set(cap.gpu_assignments_snapshot().values()) <= {70}

    def test_second_exclusive_rejected(self):
        cap = _cap(gpu_slot_count=1)
        assert cap.reserve_exclusive(80) is not None
        assert cap.reserve_exclusive(81) is None

    def test_blender_reservation_is_not_exclusive(self):
        cap = _cap(gpu_slot_count=1, cpu_slot_count=0)
        cap.reserve_for_job(90, 'GPU')
        assert not cap.has_exclusive_reservation()


class TestUnrealCards:
    """Card selection C-27 b: the exclusive reservation holds the Unreal cards in use, Blender slots or not."""

    UNREAL = 'pci:0000:23:00.0'

    def test_no_blender_gpu_slot_holds_the_unreal_card_and_the_cpu_slot(self):
        cap = _cap(gpu_slot_count=0, cpu_slot_count=1)   # a CPU-method worker
        slot = cap.reserve_exclusive(55, unreal_keys=(self.UNREAL,))
        assert slot is not None and slot.device_used == 'GPU'
        assert cap.gpu_assignments_snapshot() == {self.UNREAL: 55} and cap._cpu_slot_job_id == 55
        assert cap.is_full() and cap.free_device_prefs() == [] and cap.has_exclusive_reservation()
        cap.release_slot(55)
        assert cap.has_no_reservations() and cap.free_device_prefs() == ['CPU', 'ANY']

    def test_no_slot_at_all_still_holds_the_unreal_card(self):
        cap = _cap(gpu_slot_count=0, cpu_slot_count=0)   # method gpu, the only ticked card has no Blender match
        assert cap.reserve_exclusive(56, unreal_keys=(self.UNREAL,)) is not None
        assert cap.has_exclusive_reservation() and not cap.has_no_reservations()

    def test_blender_cards_and_unreal_cards_are_marked_once_each(self):
        cap = _cap(gpu_slot_count=2, cpu_slot_count=1)
        assert cap.reserve_exclusive(57, unreal_keys=(key(1), self.UNREAL)) is not None
        assert cap.gpu_assignments_snapshot() == {key(0): 57, key(1): 57, self.UNREAL: 57}

    def test_nothing_to_mark_or_a_pending_rebuild_reserves_nothing(self):
        from sethlans_worker_agent.capacity import rebuild
        cap = _cap(gpu_slot_count=0, cpu_slot_count=1)
        assert cap.reserve_exclusive(58) is None and cap.has_no_reservations()
        rebuild.request_rebuild()
        assert cap.reserve_exclusive(58, unreal_keys=(self.UNREAL,)) is None and cap.has_no_reservations()

    def test_the_hold_blocks_a_profile_swap(self):
        from sethlans_worker_agent.capacity import rebuild
        cap = _cap(gpu_slot_count=0, cpu_slot_count=1)
        cap.reserve_exclusive(59, unreal_keys=(self.UNREAL,))
        rebuild.request_rebuild()
        assert rebuild.step(cap) == rebuild.WAITING
        assert cap.try_rebuild(profile(gpu_slot_count=1), rebuild.generation()) == rebuild.WAITING


def test_no_drain_module():
    """D1 / FR-WX-11: the v2 drain is not built."""
    import sethlans_worker_agent
    package_dir = Path(sethlans_worker_agent.__file__).parent
    assert not (package_dir / 'unreal_executor' / 'drain.py').exists()
