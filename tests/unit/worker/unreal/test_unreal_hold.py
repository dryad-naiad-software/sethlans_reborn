# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Per-card Unreal hold (render per card FR-PC-CAP-1, -2, -4; D1, D2, D4, D10; AC-1, AC-2).

Holds by PCI address, Blender beside Unreal, the combined and CPU rules,
the shared mark, retain and ``holds_snapshot``.
"""
import threading
from pathlib import Path

from sethlans_worker_agent.capacity import WorkerCapacity, rebuild
from sethlans_worker_agent.capacity.unreal_hold import retained_owner

from ..executors._fakes import key, profile

UNREAL_ONLY = 'pci:0000:23:00.0'      # a ticked card Blender does not drive
SAME_ADDRESS = 'pci:00000000:23:00.0'  # the same address, a longer domain


def _cap(**kwargs):
    return WorkerCapacity(profile(**kwargs))


class TestHoldsByAddress:

    def test_two_holds_on_two_keys_and_no_third(self):
        cap = _cap(gpu_slot_count=2, cpu_slot_count=0)
        first, second = cap.reserve_unreal_card(1, key(0)), cap.reserve_unreal_card(2, key(1))
        assert first.card_keys == (key(0),) and second.card_keys == (key(1),)
        assert first.device_used == second.device_used == 'GPU'
        assert cap.gpu_assignments_snapshot() == {key(0): 1, key(1): 2}
        assert cap.free_keys((key(0), key(1))) == () and not cap.can_take_unreal((key(0), key(1)))
        assert cap.reserve_unreal_card(3, key(0)) is None
        cap.release_slot(1)
        assert cap.free_keys((key(0), key(1))) == (key(0),) and cap.can_take_unreal((key(0), key(1)))

    def test_the_same_address_in_two_spellings_is_refused(self):
        cap = _cap(gpu_slot_count=0, cpu_slot_count=0)
        assert cap.reserve_unreal_card(1, UNREAL_ONLY) is not None
        assert cap.reserve_unreal_card(2, SAME_ADDRESS) is None
        assert cap.free_keys((SAME_ADDRESS,)) == () and not cap.can_take_unreal((SAME_ADDRESS,))
        assert cap.gpu_assignments_snapshot() == {UNREAL_ONLY: 1}

    def test_an_unreadable_key_is_never_free_and_never_held(self):
        cap = _cap(gpu_slot_count=0, cpu_slot_count=0)
        unreadable = ('pci:0000:2A:00.0', 'apple-gpu', 'garbage', '', None)
        assert cap.free_keys(unreadable) == () and not cap.can_take_unreal(unreadable)
        assert all(cap.reserve_unreal_card(9, bad) is None for bad in unreadable)
        assert cap.has_no_reservations()

    def test_free_keys_keeps_the_given_order(self):
        cap = _cap(gpu_slot_count=3, cpu_slot_count=0)
        cap.reserve_unreal_card(1, key(1))
        assert cap.free_keys((key(2), key(1), key(0))) == (key(2), key(0))

    def test_a_pending_rebuild_refuses_the_hold(self):
        cap = _cap(gpu_slot_count=1, cpu_slot_count=0)
        rebuild.request_rebuild()
        assert not cap.can_take_unreal((key(0),))
        assert cap.reserve_unreal_card(1, key(0)) is None and cap.has_no_reservations()

    def test_one_job_holds_one_card(self):
        cap = _cap(gpu_slot_count=2, cpu_slot_count=0)
        assert cap.reserve_unreal_card(1, key(0)) is not None
        assert cap.reserve_unreal_card(1, key(1)) is None

    def test_racing_spellings_of_one_address_give_one_hold(self):
        cap = _cap(gpu_slot_count=0, cpu_slot_count=0)
        barrier, results = threading.Barrier(8), []

        def claim(job_id):
            barrier.wait()
            results.append(cap.reserve_unreal_card(job_id, UNREAL_ONLY if job_id % 2 else SAME_ADDRESS))

        threads = [threading.Thread(target=claim, args=(job_id,)) for job_id in range(1, 9)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sum(result is not None for result in results) == 1
        assert len(cap.gpu_assignments_snapshot()) == 1


class TestBlenderBesideUnreal:

    def test_a_split_job_takes_the_free_card_never_the_held_one(self):
        cap = _cap(gpu_slot_count=2, cpu_slot_count=0)
        cap.reserve_unreal_card(1, key(0))
        assert 'GPU' in cap.free_device_prefs() and not cap.is_full()
        assert cap.reserve_for_job(10, 'GPU').card_keys == (key(1),)
        assert cap.reserve_for_job(11, 'GPU') is None and cap.is_full()

    def test_unreal_takes_the_card_a_split_job_left_free(self):
        cap = _cap(gpu_slot_count=2, cpu_slot_count=0)
        cap.reserve_for_job(10, 'GPU')  # key(0), address order
        assert cap.free_keys((key(0), key(1))) == (key(1),)
        assert cap.reserve_unreal_card(1, key(0)) is None
        assert cap.reserve_unreal_card(1, key(1)) is not None

    def test_a_combined_job_never_runs_beside_unreal_on_one_of_its_cards(self):
        cap = _cap(gpu_slot_count=1, card_count=2, gpu_mode='combined', cpu_slot_count=0)
        cap.reserve_unreal_card(1, key(1))
        assert cap.reserve_for_job(10, 'GPU') is None and 'GPU' not in cap.free_device_prefs()
        cap.release_slot(1)
        assert cap.reserve_for_job(10, 'GPU').card_keys == (key(0), key(1))
        assert cap.reserve_unreal_card(2, key(0)) is None and not cap.can_take_unreal((key(0), key(1)))

    def test_a_combined_job_leaves_a_card_blender_does_not_drive(self):
        cap = _cap(gpu_slot_count=1, card_count=2, gpu_mode='combined', cpu_slot_count=0)
        cap.reserve_for_job(10, 'GPU')
        assert cap.reserve_unreal_card(1, UNREAL_ONLY) is not None


class TestCpuRule:
    """D2: no Blender CPU job beside Unreal and no Unreal job beside a Blender CPU job."""

    def test_unreal_takes_the_cpu_slot_away(self):
        cap = _cap(gpu_slot_count=2, cpu_slot_count=1)
        cap.reserve_unreal_card(1, key(0))
        assert cap.reserve_for_job(10, 'CPU') is None
        assert cap.free_device_prefs() == ['GPU', 'ANY']
        assert cap.reserve_for_job(11, 'ANY').device_used == 'GPU'
        assert cap.reserve_for_job(12, 'ANY') is None and cap.is_full()
        cap.release_slot(1)
        assert cap.reserve_for_job(10, 'CPU').device_used == 'CPU'

    def test_a_cpu_only_worker_is_full_while_unreal_runs(self):
        cap = _cap(gpu_slot_count=0, cpu_slot_count=1)
        cap.reserve_unreal_card(1, UNREAL_ONLY)
        assert cap.is_full() and cap.free_device_prefs() == []

    def test_a_blender_cpu_job_keeps_unreal_out(self):
        cap = _cap(gpu_slot_count=1, cpu_slot_count=1)
        cap.reserve_for_job(10, 'CPU')
        assert not cap.can_take_unreal((key(0),))
        assert cap.reserve_unreal_card(1, key(0)) is None
        cap.release_slot(10)
        assert cap.reserve_unreal_card(1, key(0)) is not None

    def test_a_blender_gpu_job_does_not_keep_unreal_out(self):
        cap = _cap(gpu_slot_count=1, cpu_slot_count=1)
        cap.reserve_for_job(10, 'GPU')
        assert cap.reserve_unreal_card(1, UNREAL_ONLY) is not None


class TestSharedMark:
    """D10 / FR-PC-CAP-4: shared when another reservation was held at any moment of the job's own."""

    def test_alone_is_not_shared(self):
        cap = _cap(gpu_slot_count=2, cpu_slot_count=1)
        cap.reserve_unreal_card(1, key(0))
        assert not cap.was_shared(1)

    def test_a_job_inside_the_pilot_lifetime_marks_it(self):
        cap = _cap(gpu_slot_count=2, cpu_slot_count=1)
        cap.reserve_unreal_card(1, key(0))     # the pilot, alone at its start
        cap.reserve_for_job(10, 'GPU')         # a Blender job starts ...
        assert cap.was_shared(10)
        cap.release_slot(10)                   # ... and ends inside the pilot's lifetime
        assert cap.was_shared(1) and not cap.was_shared(10)

    def test_a_job_started_beside_a_running_one_marks_both(self):
        cap = _cap(gpu_slot_count=2, cpu_slot_count=0)
        cap.reserve_unreal_card(1, key(0))
        cap.reserve_unreal_card(2, key(1))
        assert cap.was_shared(1) and cap.was_shared(2)

    def test_the_mark_is_dropped_at_release(self):
        cap = _cap(gpu_slot_count=2, cpu_slot_count=0)
        cap.reserve_for_job(10, 'GPU')
        cap.reserve_unreal_card(1, key(1))
        cap.release_slot(1)
        cap.release_slot(10)
        cap.reserve_unreal_card(1, key(1))  # the same job claimed again, alone
        assert not cap.was_shared(1)


class TestRetain:

    def test_retain_keeps_the_card_the_hold_and_the_cpu_rule(self):
        cap = _cap(gpu_slot_count=2, cpu_slot_count=1)
        cap.reserve_unreal_card(7, key(0))
        cap.reserve_for_job(10, 'GPU')
        cap.retain_slots(7)
        owner = retained_owner(7)
        assert cap.gpu_assignments_snapshot() == {key(0): owner, key(1): 10}
        assert cap.holds_snapshot()[key(0)] == (owner, 'UNREAL') and cap.has_unreal_job()
        assert cap.was_shared(owner) and not cap.was_shared(7)
        assert cap.free_keys((key(0),)) == () and cap.reserve_for_job(11, 'CPU') is None

    def test_a_reclaim_of_the_same_job_cannot_free_the_retained_card(self):
        cap = _cap(gpu_slot_count=2, cpu_slot_count=0)
        cap.reserve_unreal_card(7, key(0))
        cap.retain_slots(7)
        assert cap.reserve_unreal_card(7, key(1)) is not None
        cap.release_slot(7)
        assert cap.gpu_assignments_snapshot() == {key(0): retained_owner(7)} and cap.has_unreal_job()

    def test_a_retained_renderer_reservation_retains_the_hold(self):
        from sethlans_worker_agent.executors import RendererReservation
        from sethlans_worker_agent.job_resources import release_reservations
        cap = _cap(gpu_slot_count=1, cpu_slot_count=0)
        cap.reserve_unreal_card(7, key(0))
        reservation = RendererReservation()
        reservation.retain_until_restart()
        assert release_reservations(7, reservation, cap) is False
        assert cap.holds_snapshot() == {key(0): (retained_owner(7), 'UNREAL')}


class TestSnapshots:

    def test_holds_snapshot_names_the_renderer_of_each_card(self):
        cap = _cap(gpu_slot_count=2, cpu_slot_count=1)
        cap.reserve_unreal_card(1, key(0))
        cap.reserve_for_job(2, 'GPU')
        assert cap.reserve_for_job(3, 'CPU') is None  # D2
        assert cap.holds_snapshot() == {key(0): (1, 'UNREAL'), key(1): (2, 'BLENDER')}
        cap.release_slot(1)
        assert cap.holds_snapshot() == {key(1): (2, 'BLENDER')} and not cap.has_unreal_job()

    def test_has_no_reservations(self):
        cap = _cap(gpu_slot_count=1, cpu_slot_count=1)
        assert cap.has_no_reservations()
        cap.reserve_unreal_card(1, UNREAL_ONLY)
        assert not cap.has_no_reservations()
        cap.release_slot(1)
        assert cap.has_no_reservations() and cap.free_device_prefs() == ['GPU', 'CPU', 'ANY']


def test_the_whole_machine_reservation_is_gone():
    """FR-PC-CAP-1: ``capacity/exclusive.py`` is replaced by ``capacity/unreal_hold.py``."""
    import sethlans_worker_agent
    package_dir = Path(sethlans_worker_agent.__file__).parent
    assert not (package_dir / 'capacity' / 'exclusive.py').exists()
    assert not (package_dir / 'unreal_executor' / 'drain.py').exists()
    assert not hasattr(WorkerCapacity, 'reserve_exclusive') and not hasattr(WorkerCapacity, 'has_exclusive_reservation')
