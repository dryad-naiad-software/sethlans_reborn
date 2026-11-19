# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Card check and rebuild rules not covered elsewhere (card selection chunk 4: FR-CK-3, FR-CL-4, C-24).

- Which running jobs a check stops (C-24 e): ``lost``, ``changed`` and
  ``held`` stop any job on the card, ``blender_lost`` only a Blender job;
  an unticked or ``unknown`` card and a retained owner stop nothing, and
  unticking a card lets its job finish before the rebuild swaps.
- The swap with a key retained until restart on a removed card while a job
  keeps running on a kept card (FR-CL-4).
- One Blender list for the check and the rebuild it asks for (C-24 c).
- A rebuild whose OS probe failed alerts and stops nothing (C-24 a).
- A failed ``add_alert`` at start still completes the start check (C-24 g).
- A ``card_changed`` alert dismissed while the model still differs is
  raised again at the next check (C4).
"""
import logging

import pytest

from sethlans_worker_agent import blender_stop, job_processor, system_monitor, unreal_bridge
from sethlans_worker_agent.capacity import WorkerCapacity, rebuild
from sethlans_worker_agent.capacity.exclusive import retained_owner
from sethlans_worker_agent.cards import choice, matching, startup_log, store
from sethlans_worker_agent.config_store.io import ConfigLockTimeoutError
from tests.unit.worker._card_check_env import CardEnv
from tests.unit.worker.executors._fakes import blender_job, fake_cards, key, profile

CHECK = 'sethlans_worker_agent.capacity.card_check'


def _critical(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno == logging.CRITICAL]


def _stopped(jobs):
    return sorted(job_id for job_id, job in jobs.items() if job[blender_stop.CARD_STOP_KEY].is_set())


@pytest.fixture
def two_jobs():
    """Two cards in use; job 11 holds card 0 and job 12 holds card 1."""
    cap = WorkerCapacity(profile(gpu_slot_count=2))
    cap.reserve_for_job(11, 'GPU')
    cap.reserve_for_job(12, 'GPU')
    assert cap.gpu_assignments_snapshot() == {key(0): 11, key(1): 12}
    return cap


@pytest.fixture
def cpu_count(mocker):
    mocker.patch.object(system_monitor, 'get_cpu_thread_count', return_value=8)


class TestJobsOnUnusableCards:
    """C-24 (e): a running job is stopped only for a real fault of its card."""

    @pytest.mark.parametrize('state, stopped', [
        (matching.LOST, [12]), (matching.CHANGED, [12]), (matching.HELD, [12]), (matching.BLENDER_LOST, [12]),
        (matching.OK, []), (matching.UNTICKED, []), (matching.UNKNOWN, []), (None, []),
    ])
    def test_blender_job_by_state_of_its_card(self, two_jobs, state, stopped):
        assert two_jobs.jobs_on_unusable_cards({key(0): matching.OK, key(1): state}) == stopped

    @pytest.mark.parametrize('state, stopped', [
        (matching.BLENDER_LOST, []), (matching.LOST, [7]), (matching.CHANGED, [7]), (matching.HELD, [7]),
        (matching.OK, []), (matching.UNTICKED, []), (matching.UNKNOWN, []),
    ])
    def test_unreal_job_by_the_state_of_the_card_it_chose(self, monkeypatch, state, stopped):
        """C-24 m, D19: only the Unreal job's own card counts, and Unreal may keep a ``blender_lost`` card."""
        cap = WorkerCapacity(profile(gpu_slot_count=2))
        assert cap.reserve_exclusive(7) is not None
        monkeypatch.setattr(unreal_bridge, 'unreal_card_in_use', lambda: (7, key(1)))
        assert cap.jobs_on_unusable_cards({key(0): matching.OK, key(1): state}) == stopped

    def test_unreal_marks_on_other_cards_stop_nothing(self, monkeypatch):
        """C-24 m: the whole-machine marks on the Blender slots are not the card Unreal renders on."""
        cap = WorkerCapacity(profile(gpu_slot_count=2))
        cap.reserve_exclusive(7)
        monkeypatch.setattr(unreal_bridge, 'unreal_card_in_use', lambda: (7, key(0)))
        assert cap.jobs_on_unusable_cards({key(0): matching.OK, key(1): matching.LOST}) == []
        monkeypatch.setattr(unreal_bridge, 'unreal_card_in_use', lambda: None)  # before its first launch
        assert cap.jobs_on_unusable_cards({key(0): matching.LOST, key(1): matching.LOST}) == []

    def test_retained_owner_is_never_stopped(self, two_jobs):
        two_jobs.retain_slots(12)
        assert two_jobs.gpu_assignments_snapshot()[key(1)] == retained_owner(12)
        assert two_jobs.jobs_on_unusable_cards({key(0): matching.OK, key(1): matching.LOST}) == []

    def test_unticked_card_lets_its_job_finish_then_the_rebuild_swaps(self, monkeypatch, two_jobs, cpu_count):
        """D7: unticking stops nothing; the rebuild waits for the job on the unticked card."""
        machine = CardEnv(monkeypatch, count=2)
        jobs = {job_id: blender_job(job_id) for job_id in (11, 12)}
        for job in jobs.values():
            blender_stop.register_card_stop(job)
        machine.compute['cards'] = [machine.record(0)]  # card 1 unticked by a save

        assert two_jobs.assert_gpu_list_unchanged() is None

        assert _stopped(jobs) == [] and machine.alerts() == []
        assert rebuild.pending() is True
        assert rebuild.step(two_jobs) == rebuild.WAITING
        two_jobs.release_slot(12)  # job 12 finished on its own
        assert rebuild.step(two_jobs) == rebuild.SWAPPED
        assert two_jobs.profile.cards == fake_cards(1)
        assert two_jobs.gpu_assignments_snapshot() == {key(0): 11}


class TestSwapWithRetainedKey:
    """FR-CL-4: a key retained until restart on a removed card does not block, a kept card's job runs on."""

    def test_retained_key_on_removed_card_and_job_on_kept_card(self, two_jobs):
        two_jobs.retain_slots(12)  # job 12's Blender would not die; job 11 still renders on card 0
        new = profile(gpu_slot_count=1)
        rebuild.request_rebuild()

        assert two_jobs.try_rebuild(new, rebuild.generation()) == rebuild.SWAPPED

        assert two_jobs.profile is new and rebuild.pending() is False
        assert two_jobs.gpu_assignments_snapshot() == {key(0): 11, key(1): retained_owner(12)}
        assert two_jobs.free_device_prefs() == ['CPU', 'ANY']  # card 0 busy; card 1 is not in the profile
        two_jobs.release_slot(11)
        assert two_jobs.free_device_prefs() == ['GPU', 'CPU', 'ANY']  # the retained key on card 1 does not count
        assert two_jobs.reserve_for_job(13, 'GPU').card_keys == (key(0),)


class TestOneBlenderList:
    """C-24 (c): the rebuild uses the Blender list of the check that asked for it."""

    def test_card_newly_offered_by_blender_comes_into_use_once(self, monkeypatch, cpu_count):
        machine = CardEnv(monkeypatch, count=2)
        # The start cache did not offer card 1 (e.g. its driver came later); a fresh detection does.
        monkeypatch.setattr(startup_log, 'cached_blender_list', lambda: (machine.blender_records({key(0)}), '4.5.1'))
        cap = WorkerCapacity(profile(gpu_slot_count=1))

        assert cap.assert_gpu_list_unchanged() is None
        assert rebuild.pending() is True and machine.detections == 1
        assert rebuild.step(cap) == rebuild.SWAPPED
        assert cap.profile.cards == fake_cards(2) and cap.profile.gpu_slot_count == 2

        assert cap.assert_gpu_list_unchanged() is None
        assert rebuild.pending() is False  # the rebuilt profile matches the check: no rebuild every heartbeat
        assert machine.alerts() == []


class TestFailedProbeInTheRebuild:
    """C-24 (a): a ticked key absent from an inventory built from Blender is ``unknown``: no alert, no stop."""

    @pytest.fixture
    def machine(self, monkeypatch, two_jobs, cpu_count):
        machine = CardEnv(monkeypatch, count=2)
        machine.jobs = {job_id: blender_job(job_id) for job_id in (11, 12)}
        for job in machine.jobs.values():
            blender_stop.register_card_stop(job)
        return machine

    def _fail_and_lose_card_1(self, machine):
        machine.fail_probe = True
        machine.lose(1)  # the Blender list the inventory falls back to has no card 1
        rebuild.request_rebuild()

    def _nothing_raised_or_stopped(self, machine):
        assert machine.alerts() == [] and machine.writes == [] and store.snapshot().alerts == ()
        assert _stopped(machine.jobs) == []

    def test_probe_that_never_worked_builds_from_blender(self, machine, two_jobs):
        self._fail_and_lose_card_1(machine)

        assert rebuild.step(two_jobs) == rebuild.WAITING  # job 12 still holds card 1

        assert machine.probes == 1
        self._nothing_raised_or_stopped(machine)

    def test_probe_that_worked_before_keeps_the_rebuild_pending(self, machine, two_jobs):
        assert two_jobs.assert_gpu_list_unchanged() is None  # a working probe in this process
        self._fail_and_lose_card_1(machine)

        assert rebuild.step(two_jobs) is None

        assert rebuild.pending() is True and two_jobs.profile.cards == fake_cards(2)
        self._nothing_raised_or_stopped(machine)


class TestStartWithFailedAlertWrite:
    """C-24 (g), C5: the start check completes; the alert is kept in memory and logged once."""

    @pytest.mark.parametrize('error', [ConfigLockTimeoutError('lock'), OSError('disk full')], ids=['lock', 'os'])
    def test_start_completes_and_reports_from_memory(self, monkeypatch, mocker, caplog, cpu_count, error):
        mocker.patch.object(system_monitor, 'get_gpu_device_details', return_value=[])
        machine = CardEnv(monkeypatch, count=2)
        machine.lose(1)
        machine.fail_write = error

        with caplog.at_level(logging.CRITICAL, logger=CHECK):
            cap = job_processor.init_capacity()

        assert cap.profile.cards == fake_cards(1)
        assert machine.alerts() == []
        assert [(a['kind'], a['key']) for a in store.snapshot().alerts] == [('card_lost', key(1))]
        assert len(_critical(caplog)) == 1


class TestChangedDismissedWhileChanged:
    """C4: the level-triggered check raises ``card_changed`` again after a dismissal."""

    def test_realerted_at_the_next_check(self, monkeypatch, caplog):
        machine = CardEnv(monkeypatch, count=2)
        machine.compute['cards'][0]['device_id'] = 0x1234  # saved as another model than the one present
        cap = WorkerCapacity(profile(gpu_slot_count=2))
        cap.assert_gpu_list_unchanged()
        [alert] = machine.alerts()
        assert (alert['kind'], alert['key']) == ('card_changed', key(0))

        choice.dismiss_alerts([alert['id']], 'manager')
        assert machine.alerts() == []
        caplog.clear()
        with caplog.at_level(logging.CRITICAL, logger=CHECK):
            assert cap.assert_gpu_list_unchanged() is None

        assert machine.alert_keys() == [('card_changed', key(0))]
        assert machine.alerts()[0]['id'] != alert['id']
        assert len(_critical(caplog)) == 1
