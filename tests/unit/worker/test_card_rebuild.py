# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The live slot rebuild (FR-CL-4, C1, C2, AC-8 unit shape, AC-42).

- ``request_rebuild`` works before any capacity exists and takes only
  ``rebuild._lock``; while a rebuild is pending no GPU (or Unreal) slot is free.
- ``try_rebuild`` stays pending when the generation moved, waits while a
  running job holds a card that leaves use (or any card when the GPU mode
  changes, or the whole machine), and otherwise swaps the profile: jobs on
  kept cards keep running and keys retained until restart do not block.
- ``step`` builds the profile through ``card_check.rebuild_profile`` only
  while pending, and not again while nothing changed since a blocked attempt.
"""
import dataclasses
import threading

import pytest

from sethlans_worker_agent import blender_stop, job_processor
from sethlans_worker_agent.capacity import WorkerCapacity, card_check, rebuild
from sethlans_worker_agent.capacity.exclusive import retained_owner
from tests.unit.worker._card_check_env import CardEnv
from tests.unit.worker.executors._fakes import blender_job, fake_cards, key, profile

WAIT = 5.0  # bound for every join; never reached when the code is right


def _without_card(prof, index):
    cards = tuple(card for card in prof.cards if card.key != key(index))
    return dataclasses.replace(prof, cards=cards, gpu_slot_count=len(cards))


class TestPendingMark:

    def test_request_before_any_capacity_exists(self):
        assert job_processor.get_worker_capacity() is None
        rebuild.request_rebuild()
        rebuild.request_rebuild()
        assert rebuild.state() == (2, True)

    def test_pending_rebuild_frees_no_gpu_slot(self):
        cap = WorkerCapacity(profile(gpu_slot_count=2))
        rebuild.request_rebuild()

        assert cap.free_device_prefs() == ['CPU', 'ANY']
        assert cap.reserve_for_job(1, 'GPU') is None
        assert cap.reserve_for_job(2, 'ANY').device_used == 'CPU'  # the CPU slot is unaffected
        assert cap.reserve_exclusive(3) is None  # no Unreal claim either

    def test_request_does_not_wait_for_the_capacity_lock(self):
        """Lock order: request_rebuild takes only rebuild._lock (innermost)."""
        cap = WorkerCapacity(profile(gpu_slot_count=1))
        done = threading.Event()
        with cap._lock:
            thread = threading.Thread(target=lambda: (rebuild.request_rebuild(), done.set()))
            thread.start()
            assert done.wait(WAIT)
        thread.join(WAIT)
        assert rebuild.pending() is True

    def test_mark_built_clears_only_its_own_generation(self):
        rebuild.request_rebuild()
        built_from = rebuild.generation()
        rebuild.request_rebuild()  # moved while the capacity was built
        rebuild.mark_built(built_from)
        assert rebuild.pending() is True
        rebuild.mark_built(rebuild.generation())
        assert rebuild.pending() is False


class TestTryRebuild:

    @pytest.fixture
    def cap(self):
        """Two cards in use; each test reserves first, then a rebuild is requested."""
        return WorkerCapacity(profile(gpu_slot_count=2))

    def test_generation_moved_meanwhile_stays_pending(self, cap):
        """AC-42 / C1."""
        built_from = rebuild.generation()
        rebuild.request_rebuild()

        assert cap.try_rebuild(_without_card(cap.profile, 1), built_from) == rebuild.MOVED

        assert rebuild.pending() is True and len(cap.profile.cards) == 2

    def test_job_on_a_removed_card_blocks_then_the_swap_keeps_the_other_job(self, cap):
        cap.reserve_for_job(11, 'GPU')
        cap.reserve_for_job(12, 'GPU')
        new = _without_card(cap.profile, 1)
        rebuild.request_rebuild()

        assert cap.try_rebuild(new, rebuild.generation()) == rebuild.WAITING
        assert rebuild.rebuild_waiting() is True and cap.profile is not new

        cap.release_slot(12)
        assert cap.try_rebuild(new, rebuild.generation()) == rebuild.SWAPPED

        assert cap.profile is new and rebuild.pending() is False and rebuild.rebuild_waiting() is False
        assert cap.gpu_assignments_snapshot() == {key(0): 11}
        assert cap.free_device_prefs() == ['CPU', 'ANY']  # card 0 still busy, card 1 gone

    def test_jobs_on_kept_cards_do_not_block(self, cap):
        cap.reserve_for_job(11, 'GPU')
        assert cap.try_rebuild(_without_card(cap.profile, 1), rebuild.generation()) == rebuild.SWAPPED

    def test_retained_key_on_a_removed_card_does_not_block(self, cap):
        cap.reserve_for_job(11, 'GPU')
        cap.reserve_for_job(12, 'GPU')
        cap.release_slot(11)
        cap.retain_slots(12)
        assert cap.gpu_assignments_snapshot() == {key(1): retained_owner(12)}

        assert cap.try_rebuild(_without_card(cap.profile, 1), rebuild.generation()) == rebuild.SWAPPED

    def test_gpu_mode_change_waits_for_any_running_gpu_job(self, cap):
        cap.reserve_for_job(11, 'GPU')
        combined = dataclasses.replace(cap.profile, gpu_mode='combined', gpu_slot_count=1)
        rebuild.request_rebuild()

        assert cap.try_rebuild(combined, rebuild.generation()) == rebuild.WAITING
        cap.release_slot(11)
        assert cap.try_rebuild(combined, rebuild.generation()) == rebuild.SWAPPED

    def test_whole_machine_reservation_blocks(self):
        cap = WorkerCapacity(profile(gpu_slot_count=1))
        cap.reserve_exclusive(7)
        rebuild.request_rebuild()
        more = profile(gpu_slot_count=2)

        assert cap.try_rebuild(more, rebuild.generation()) == rebuild.WAITING
        cap.release_slot(7)
        assert cap.try_rebuild(more, rebuild.generation()) == rebuild.SWAPPED

    def test_cpu_slot_holder_is_kept(self, cap):
        assert cap.reserve_for_job(5, 'CPU').device_used == 'CPU'
        gpu_only = dataclasses.replace(cap.profile, cpu_slot_count=0, method='gpu')

        assert cap.try_rebuild(gpu_only, rebuild.generation()) == rebuild.SWAPPED

        assert cap._cpu_slot_job_id == 5
        cap.release_slot(5)
        assert cap._cpu_slot_job_id is None


class TestStep:

    def test_nothing_happens_without_a_pending_rebuild(self, mocker):
        build = mocker.patch.object(card_check, 'rebuild_profile')
        assert rebuild.step(WorkerCapacity(profile(gpu_slot_count=1))) is None
        build.assert_not_called()

    def test_request_during_the_build_leaves_the_rebuild_pending(self, mocker):
        """AC-42 / C1: a request between reading the generation and try_rebuild."""
        cap = WorkerCapacity(profile(gpu_slot_count=2))
        rebuild.request_rebuild()
        new = _without_card(cap.profile, 1)

        def build(capacity):
            rebuild.request_rebuild()  # e.g. a page save on a web UI thread
            return new
        mocker.patch.object(card_check, 'rebuild_profile', side_effect=build)

        assert rebuild.step(cap) == rebuild.MOVED
        assert rebuild.pending() is True and cap.profile is not new

        mocker.patch.object(card_check, 'rebuild_profile', return_value=new)
        assert rebuild.step(cap) == rebuild.SWAPPED and cap.profile is new

    def test_blocked_attempt_is_not_rebuilt_until_something_changes(self, mocker):
        cap = WorkerCapacity(profile(gpu_slot_count=2))
        cap.reserve_for_job(12, 'GPU')
        cap.reserve_for_job(13, 'GPU')
        rebuild.request_rebuild()
        build = mocker.patch.object(card_check, 'rebuild_profile', return_value=_without_card(cap.profile, 1))

        assert [rebuild.step(cap) for _ in range(3)] == [rebuild.WAITING] * 3
        assert build.call_count == 1

        cap.release_slot(13)
        assert rebuild.step(cap) == rebuild.SWAPPED and build.call_count == 2

    def test_unreadable_settings_stay_pending(self, monkeypatch):
        machine = CardEnv(monkeypatch, count=2)
        machine.fail_read = True
        cap = WorkerCapacity(profile(gpu_slot_count=2))
        rebuild.request_rebuild()

        assert rebuild.step(cap) is None
        assert rebuild.pending() is True and machine.probes == 0

    def test_error_while_building_stays_pending(self, mocker):
        mocker.patch.object(card_check, 'rebuild_profile', side_effect=RuntimeError('boom'))
        rebuild.request_rebuild()
        assert rebuild.step(WorkerCapacity(profile(gpu_slot_count=1))) is None
        assert rebuild.pending() is True


class TestLostCardFlow:
    """AC-8 (unit shape): stop only card 1's job; swap only after it released; card 0's job runs on."""

    def test_lost_card_rebuild_waits_for_its_job_only(self, monkeypatch):
        machine = CardEnv(monkeypatch, count=2)
        cap = WorkerCapacity(profile(gpu_slot_count=2))
        jobs = {job_id: blender_job(job_id) for job_id in (11, 12)}
        for job_id, job in jobs.items():
            cap.reserve_for_job(job_id, 'GPU')
            blender_stop.register_card_stop(job)
        machine.lose(1)

        assert cap.assert_gpu_list_unchanged() is None
        assert [j for j, job in jobs.items() if job[blender_stop.CARD_STOP_KEY].is_set()] == [12]

        assert rebuild.step(cap) == rebuild.WAITING
        assert rebuild.rebuild_waiting() is True and len(cap.profile.cards) == 2

        cap.release_slot(12)  # the stopped job handed back and released its slot
        assert rebuild.step(cap) == rebuild.SWAPPED

        assert cap.profile.cards == fake_cards(1) and cap.profile.gpu_slot_count == 1
        assert cap.gpu_assignments_snapshot() == {key(0): 11}
        assert not jobs[11][blender_stop.CARD_STOP_KEY].is_set()
        assert machine.alert_keys() == [('card_lost', key(1))]
