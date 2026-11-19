# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for WorkerCapacity slot state.

Covers FR-27: reserve/release, idempotency, is_full, free_device_prefs,
gpu_assignments_snapshot, and the combined variant. GPU slots are keyed
by card key (FR-CL-3).
"""
from sethlans_worker_agent.capacity import (
    SlotReservation,
    WorkerCapacity,
)

from .executors._fakes import key, profile


def _profile(gpu_slot_count=1, cpu_slot_count=1, startup_gpu_count=None, gpu_mode='split'):
    """Split: one card per GPU slot; combined: ``startup_gpu_count`` cards behind one slot."""
    card_count = startup_gpu_count if gpu_mode == 'combined' else None
    return profile(gpu_slot_count=gpu_slot_count, cpu_slot_count=cpu_slot_count,
                   card_count=card_count, gpu_mode=gpu_mode)


# --- is_full ---

class TestIsFull:
    def test_false_when_empty(self):
        cap = WorkerCapacity(_profile(gpu_slot_count=2, cpu_slot_count=1))
        assert cap.is_full() is False

    def test_false_when_gpus_used_but_cpu_free(self):
        cap = WorkerCapacity(_profile(gpu_slot_count=1, cpu_slot_count=1, startup_gpu_count=1))
        cap.reserve_for_job(1, 'GPU')
        assert cap.is_full() is False

    def test_true_when_all_slots_used(self):
        cap = WorkerCapacity(_profile(gpu_slot_count=1, cpu_slot_count=1, startup_gpu_count=1))
        cap.reserve_for_job(1, 'GPU')
        cap.reserve_for_job(2, 'CPU')
        assert cap.is_full() is True

    def test_true_when_gpu_only_worker_gpu_busy(self):
        cap = WorkerCapacity(_profile(gpu_slot_count=1, cpu_slot_count=0, startup_gpu_count=1))
        cap.reserve_for_job(1, 'GPU')
        assert cap.is_full() is True

    def test_true_when_cpu_only_worker_cpu_busy(self):
        cap = WorkerCapacity(_profile(gpu_slot_count=0, cpu_slot_count=1, startup_gpu_count=0))
        cap.reserve_for_job(1, 'CPU')
        assert cap.is_full() is True


# --- free_device_prefs ---

class TestFreeDevicePrefs:
    def test_empty_when_nothing_free(self):
        cap = WorkerCapacity(_profile(gpu_slot_count=0, cpu_slot_count=0))
        assert cap.free_device_prefs() == []

    def test_all_three_when_gpu_and_cpu_free(self):
        cap = WorkerCapacity(_profile(gpu_slot_count=1, cpu_slot_count=1))
        assert cap.free_device_prefs() == ['GPU', 'CPU', 'ANY']

    def test_gpu_and_any_when_only_gpu_free(self):
        cap = WorkerCapacity(_profile(gpu_slot_count=1, cpu_slot_count=0))
        assert cap.free_device_prefs() == ['GPU', 'ANY']

    def test_cpu_and_any_when_only_cpu_free(self):
        cap = WorkerCapacity(_profile(gpu_slot_count=1, cpu_slot_count=1, startup_gpu_count=1))
        cap.reserve_for_job(1, 'GPU')
        assert cap.free_device_prefs() == ['CPU', 'ANY']

    def test_empty_after_full(self):
        cap = WorkerCapacity(_profile(gpu_slot_count=1, cpu_slot_count=1, startup_gpu_count=1))
        cap.reserve_for_job(1, 'GPU')
        cap.reserve_for_job(2, 'CPU')
        assert cap.free_device_prefs() == []


# --- reserve_for_job ---

class TestReserveForJob:
    def test_reserve_gpu_split_returns_reservation(self):
        cap = WorkerCapacity(_profile(gpu_slot_count=2, cpu_slot_count=1, startup_gpu_count=2))
        reservation = cap.reserve_for_job(1, 'GPU')
        assert isinstance(reservation, SlotReservation)
        assert reservation.device_used == 'GPU'
        assert reservation.card_keys == (key(0),)
        assert reservation.job_cards == reservation.cards[:1]

    def test_reserve_second_gpu_uses_next_index(self):
        cap = WorkerCapacity(_profile(gpu_slot_count=2, cpu_slot_count=0, startup_gpu_count=2))
        cap.reserve_for_job(1, 'GPU')
        reservation = cap.reserve_for_job(2, 'GPU')
        assert reservation is not None
        assert reservation.card_keys == (key(1),)

    def test_reserve_cpu_returns_cpu_reservation(self):
        cap = WorkerCapacity(_profile(gpu_slot_count=0, cpu_slot_count=1))
        reservation = cap.reserve_for_job(1, 'CPU')
        assert reservation is not None
        assert reservation.device_used == 'CPU'
        assert reservation.card_keys == ()
        assert reservation.job_cards == ()

    def test_reserve_any_prefers_gpu_when_available(self):
        cap = WorkerCapacity(_profile(gpu_slot_count=1, cpu_slot_count=1, startup_gpu_count=1))
        reservation = cap.reserve_for_job(1, 'ANY')
        assert reservation is not None
        assert reservation.device_used == 'GPU'

    def test_reserve_any_falls_back_to_cpu_when_no_gpu_free(self):
        cap = WorkerCapacity(_profile(gpu_slot_count=1, cpu_slot_count=1, startup_gpu_count=1))
        cap.reserve_for_job(1, 'GPU')
        reservation = cap.reserve_for_job(2, 'ANY')
        assert reservation is not None
        assert reservation.device_used == 'CPU'

    def test_reserve_returns_none_when_gpu_full_and_no_cpu_slot(self):
        cap = WorkerCapacity(_profile(gpu_slot_count=1, cpu_slot_count=0, startup_gpu_count=1))
        cap.reserve_for_job(1, 'GPU')
        assert cap.reserve_for_job(2, 'GPU') is None

    def test_reserve_returns_none_when_cpu_full_and_no_gpu_slot(self):
        cap = WorkerCapacity(_profile(gpu_slot_count=0, cpu_slot_count=1))
        cap.reserve_for_job(1, 'CPU')
        assert cap.reserve_for_job(2, 'CPU') is None

    def test_reserve_gpu_when_cpu_only_returns_none(self):
        cap = WorkerCapacity(_profile(gpu_slot_count=0, cpu_slot_count=1))
        assert cap.reserve_for_job(1, 'GPU') is None

    def test_reserve_cpu_when_gpu_only_returns_none(self):
        cap = WorkerCapacity(_profile(gpu_slot_count=1, cpu_slot_count=0, startup_gpu_count=1))
        assert cap.reserve_for_job(1, 'CPU') is None

    def test_reserve_combined_mode_holds_every_card(self):
        cap = WorkerCapacity(_profile(gpu_slot_count=1, cpu_slot_count=0,
                                      startup_gpu_count=4, gpu_mode='combined'))
        reservation = cap.reserve_for_job(1, 'GPU')
        assert reservation is not None
        assert reservation.device_used == 'GPU'
        assert reservation.card_keys == tuple(key(i) for i in range(4))
        assert reservation.gpu_mode == 'combined'
        # Combined mode is saturated after a single reservation.
        assert cap.is_full() is True
        assert cap.reserve_for_job(2, 'GPU') is None

    def test_reserve_combined_mode_released_is_available_again(self):
        cap = WorkerCapacity(_profile(gpu_slot_count=1, cpu_slot_count=0,
                                      startup_gpu_count=4, gpu_mode='combined'))
        cap.reserve_for_job(1, 'GPU')
        cap.release_slot(1)
        assert cap.reserve_for_job(2, 'GPU') is not None


# --- release_slot ---

class TestReleaseSlot:
    def test_release_frees_gpu_slot(self):
        cap = WorkerCapacity(_profile(gpu_slot_count=1, cpu_slot_count=0, startup_gpu_count=1))
        cap.reserve_for_job(1, 'GPU')
        cap.release_slot(1)
        assert cap.gpu_assignments_snapshot() == {}
        assert cap.is_full() is False

    def test_release_frees_cpu_slot(self):
        cap = WorkerCapacity(_profile(gpu_slot_count=0, cpu_slot_count=1))
        cap.reserve_for_job(1, 'CPU')
        cap.release_slot(1)
        assert cap.is_full() is False
        assert cap.reserve_for_job(2, 'CPU') is not None

    def test_release_idempotent_second_call_is_noop(self):
        cap = WorkerCapacity(_profile(gpu_slot_count=1, cpu_slot_count=0, startup_gpu_count=1))
        cap.reserve_for_job(1, 'GPU')
        cap.release_slot(1)
        cap.release_slot(1)
        assert cap.gpu_assignments_snapshot() == {}

    def test_release_unknown_job_id_is_noop(self, caplog):
        caplog.set_level('DEBUG', logger='sethlans_worker_agent.capacity.slots')
        cap = WorkerCapacity(_profile(gpu_slot_count=1, cpu_slot_count=1, startup_gpu_count=1))
        cap.reserve_for_job(1, 'GPU')
        cap.release_slot(9999)
        assert cap.gpu_assignments_snapshot() == {key(0): 1}
        debug_msgs = [r.message for r in caplog.records if r.levelname == 'DEBUG']
        assert any('no slot held' in m for m in debug_msgs)

    def test_release_never_raises_on_internal_error(self, mocker):
        cap = WorkerCapacity(_profile(gpu_slot_count=1, cpu_slot_count=0, startup_gpu_count=1))
        cap.reserve_for_job(1, 'GPU')
        # Simulate a catastrophic internal error. Contract: never raise.
        real_assignments = cap._gpu_assignments
        broken = mocker.MagicMock()
        broken.items.side_effect = RuntimeError('disk on fire')
        cap._gpu_assignments = broken
        try:
            cap.release_slot(1)  # must not raise
        finally:
            cap._gpu_assignments = real_assignments

    def test_release_handles_combined_mode(self):
        cap = WorkerCapacity(_profile(gpu_slot_count=1, cpu_slot_count=0,
                                      startup_gpu_count=4, gpu_mode='combined'))
        cap.reserve_for_job(77, 'GPU')
        assert cap.gpu_assignments_snapshot() == {key(i): 77 for i in range(4)}
        cap.release_slot(77)
        assert cap.gpu_assignments_snapshot() == {}


# --- gpu_assignments_snapshot ---

class TestGpuAssignmentsSnapshot:
    def test_empty_when_no_reservations(self):
        cap = WorkerCapacity(_profile(gpu_slot_count=2, cpu_slot_count=0, startup_gpu_count=2))
        assert cap.gpu_assignments_snapshot() == {}

    def test_returns_copy_not_internal_dict(self):
        cap = WorkerCapacity(_profile(gpu_slot_count=2, cpu_slot_count=0, startup_gpu_count=2))
        cap.reserve_for_job(1, 'GPU')
        snap = cap.gpu_assignments_snapshot()
        snap[999] = 5
        assert 999 not in cap._gpu_assignments
