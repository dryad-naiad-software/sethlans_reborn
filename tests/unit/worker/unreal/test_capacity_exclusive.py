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

from ..executors._fakes import profile


def _cap(**kwargs):
    return WorkerCapacity(profile(**kwargs))


class TestModes:

    def test_split_marks_every_gpu_and_cpu(self):
        cap = _cap(gpu_slot_count=3, cpu_slot_count=1)
        slot = cap.reserve_exclusive(50)
        assert slot is not None
        assert cap.gpu_assignments_snapshot() == {0: 50, 1: 50, 2: 50}
        assert cap._cpu_slot_job_id == 50
        assert slot.device_used == 'GPU' and slot.gpu_indices == []

    def test_combined_marks_sentinel_key(self):
        cap = _cap(gpu_slot_count=1, startup_gpu_count=2, gpu_mode='combined')
        assert cap.reserve_exclusive(51) is not None
        assert cap.gpu_assignments_snapshot() == {0: 51}
        assert cap.is_full() and cap.free_device_prefs() == []

    def test_no_cpu_slot_means_no_cpu_mark(self):
        cap = _cap(gpu_slot_count=2, cpu_slot_count=0)
        assert cap.reserve_exclusive(52) is not None
        assert cap._cpu_slot_job_id is None
        assert cap.is_full() and cap.free_device_prefs() == []

    def test_no_gpu_slot_returns_none(self):
        cap = _cap(gpu_slot_count=0, cpu_slot_count=1, startup_gpu_count=0)
        assert cap.reserve_exclusive(53) is None
        assert cap.has_no_reservations()

    def test_forced_gpu_index_returns_none(self):
        """Dom N-4 / D10f: sibling workers own the host's other GPUs."""
        cap = _cap(gpu_slot_count=1, startup_gpu_count=2, force_gpu_index=1)
        assert cap.reserve_exclusive(55) is None
        assert cap.has_no_reservations()

    def test_single_gpu_split_has_no_primary_index(self):
        slot = _cap(gpu_slot_count=1).reserve_exclusive(54)
        assert slot.gpu_indices == []
        assert slot.primary_gpu_index is None


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


def test_no_drain_module():
    """D1 / FR-WX-11: the v2 drain is not built."""
    import sethlans_worker_agent
    package_dir = Path(sethlans_worker_agent.__file__).parent
    assert not (package_dir / 'unreal_executor' / 'drain.py').exists()
