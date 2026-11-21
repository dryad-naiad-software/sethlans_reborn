# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
An Unreal job on a card that is lost, changed or held is stopped (card selection FR-CK-3, C-24 m).

While an Unreal job holds a card the periodic card check does not run
(FR-CK-1, render per card D15). Once per heartbeat interval the OS list alone is
read instead (no Blender): the red alert is raised and the Unreal job whose
chosen card is gone gets its own stop, which the executor sees wherever it
sees a shutdown; the job is released, ``finish`` runs, and the worker's
shutdown event is never set.
"""
import threading
import time

import pytest

from sethlans_worker_agent import agent_shutdown, blender_stop, config, job_processor
from sethlans_worker_agent.blender_job_stop import JobStop
from sethlans_worker_agent.capacity import card_check, rebuild
from sethlans_worker_agent.unreal_executor import adapter_choice
from sethlans_worker_agent.unreal_executor.outcomes import Cancelled
from tests.unit.worker._card_check_env import CardEnv
from tests.unit.worker.executors._fakes import install_capacity, key, unreal_job
from tests.unit.worker.unreal._job_run_world import World


@pytest.fixture
def held(monkeypatch):
    """Two ticked cards, an Unreal job 5 holding card 1 and rendering on it."""
    monkeypatch.setattr(config, 'HEARTBEAT_INTERVAL_SECONDS', 30)
    machine = CardEnv(monkeypatch, count=2)
    cap = install_capacity(gpu_slot_count=2, cpu_slot_count=1)
    assert cap.reserve_unreal_card(5, key(1)) is not None
    machine.job = unreal_job(5)
    blender_stop.register_card_stop(machine.job)
    monkeypatch.setitem(adapter_choice._current, 'a' * 32, adapter_choice.Choice(5, key(1), 'GPU 1', 1))
    machine.cap = cap
    return machine


def _stopped(machine):
    return machine.job[blender_stop.CARD_STOP_KEY].is_set()


class TestCheckDuringTheHold:

    def test_a_lost_card_raises_the_alert_and_stops_the_unreal_job(self, held):
        held.lose(1)
        job_processor.maybe_assert_gpu_list_unchanged()

        assert _stopped(held) and held.alert_keys() == [('card_lost', key(1))]
        assert held.probes == 1 and held.detections == 0          # the OS list only, never Blender
        assert agent_shutdown._shutdown_event.is_set() is False
        assert rebuild.pending() is True
        assert rebuild.step(held.cap) == rebuild.SWAPPED           # an Unreal hold never blocks it (D15)
        assert held.cap.holds_snapshot() == {key(1): (5, 'UNREAL')}  # held until the stopped job ends

    @pytest.mark.parametrize('change', ['changed', 'held'])
    def test_a_changed_or_held_card_stops_it_too(self, held, change):
        if change == 'changed':
            held.compute['cards'][1]['device_id'] = 0x1234
        else:
            held.compute['alerts'] = [{'id': 'b' * 32, 'kind': 'card_changed', 'key': key(1), 'name': 'GPU 1',
                                       'detail': '', 'raised_at': None}]
        card_check.check_during_unreal(held.cap, 1000.0)
        assert _stopped(held)

    def test_a_card_blender_lost_keeps_the_unreal_job(self, held):
        held.compute['alerts'] = [{'id': 'c' * 32, 'kind': 'card_lost_blender', 'key': key(1), 'name': 'GPU 1',
                                   'detail': '', 'raised_at': None}]
        card_check.check_during_unreal(held.cap, 1000.0)
        assert not _stopped(held)

    def test_one_of_two_unreal_jobs_is_stopped_and_the_other_runs_on(self, held, monkeypatch):
        """Render per card FR-PC-SEL-4: only the job on the lost card gets its stop; job 6 on card 0 keeps going."""
        assert held.cap.reserve_unreal_card(6, key(0)) is not None
        other = unreal_job(6)
        blender_stop.register_card_stop(other)
        monkeypatch.setitem(adapter_choice._current, 'b' * 32, adapter_choice.Choice(6, key(0), 'GPU 0', 0))
        held.lose(1)
        card_check.check_during_unreal(held.cap, 1000.0)
        assert _stopped(held) and not other[blender_stop.CARD_STOP_KEY].is_set()
        assert held.cap.holds_snapshot() == {key(0): (6, 'UNREAL'), key(1): (5, 'UNREAL')}

    def test_another_lost_card_alerts_but_does_not_stop_it(self, held):
        held.lose(0)
        card_check.check_during_unreal(held.cap, 1000.0)
        assert not _stopped(held) and held.alert_keys() == [('card_lost', key(0))]

    def test_once_per_heartbeat_interval(self, held):
        card_check.check_during_unreal(held.cap, 1000.0)
        card_check.check_during_unreal(held.cap, 1029.0)
        assert held.probes == 1
        held.lose(1)
        card_check.check_during_unreal(held.cap, 1030.0)
        assert held.probes == 2 and _stopped(held)

    def test_a_failed_probe_or_unreadable_choice_changes_nothing(self, held):
        held.lose(1)
        held.fail_probe = True
        card_check.check_during_unreal(held.cap, 1000.0)
        held.fail_probe, held.fail_read = False, True
        card_check.check_during_unreal(held.cap, 2000.0)
        assert not _stopped(held) and held.alerts() == [] and rebuild.pending() is False

    def test_nothing_ticked_probes_nothing(self, held):
        held.compute['cards'] = []
        card_check.check_during_unreal(held.cap, 1000.0)
        assert held.probes == 0 and not _stopped(held)

    def test_the_full_check_runs_again_once_the_hold_ends(self, held):
        job_processor.maybe_assert_gpu_list_unchanged()
        assert held.detections == 0
        held.cap.release_slot(5)
        job_processor.maybe_assert_gpu_list_unchanged()
        assert held.detections == 1


class TestJobStopWait:
    """The Unreal runner sleeps on the joined event (``RunSpec.shutdown_event``)."""

    def test_wakes_for_the_jobs_own_stop(self):
        own = threading.Event()
        threading.Timer(0.05, own.set).start()
        started = time.monotonic()
        assert JobStop(threading.Event(), own).wait(5) is True
        assert time.monotonic() - started < 2

    def test_wakes_for_the_worker_shutdown(self):
        shutdown = threading.Event()
        threading.Timer(0.05, shutdown.set).start()
        assert JobStop(shutdown, threading.Event()).wait(5) is True

    def test_times_out(self):
        assert JobStop(None, threading.Event()).wait(0.05) is False


class TestCancelWatcher:
    """Concurrency review: a card stop is not a cancel cause, so a later manager CANCELED is still recorded."""

    def _watcher(self, stop, status):
        from sethlans_worker_agent.unreal_executor.cancel_watch import CancelCause, CancelWatcher
        cause = CancelCause()
        return cause, CancelWatcher(5, cause, get_assignment=lambda job_id: (status, 1), worker_id=1,
                                    shutdown_event=stop, poll_seconds=60)

    def test_the_jobs_own_stop_is_not_recorded_and_canceled_is(self):
        own = threading.Event()
        own.set()
        cause, watcher = self._watcher(JobStop(threading.Event(), own), 'CANCELED')
        watcher._check_once()
        assert cause.cause == 'canceled'

    def test_the_jobs_own_stop_alone_records_nothing(self):
        own = threading.Event()
        own.set()
        cause, watcher = self._watcher(JobStop(threading.Event(), own), 'RENDERING')
        watcher._check_once()
        assert cause.cause is None

    def test_the_worker_shutdown_still_is(self):
        shutdown = threading.Event()
        shutdown.set()
        cause, watcher = self._watcher(JobStop(shutdown, threading.Event()), 'CANCELED')
        watcher._check_once()
        assert cause.cause == 'shutdown'

    def test_a_manager_cancel_wins_over_the_card_stop_in_the_outcome(self, tmp_path, monkeypatch):
        from sethlans_worker_agent.unreal_executor import executor
        w = World(tmp_path, monkeypatch)
        own = threading.Event()
        own.set()
        w.flow_action = lambda job: (w_run.cancel.set('canceled'), (_ for _ in ()).throw(Cancelled('canceled')))[1]
        w_run = None
        from sethlans_worker_agent.unreal_executor import job_run
        original_init = job_run.JobRun.__init__

        def remember(self, *args, **kwargs):
            nonlocal w_run
            original_init(self, *args, **kwargs)
            w_run = self
        monkeypatch.setattr(job_run.JobRun, '__init__', remember)
        monkeypatch.setattr(executor, 'job_stop', lambda event, job_data: JobStop(event, own))
        result = w.run()
        assert result.was_canceled and 'release' not in w.names()


class TestExecutorHandBack:

    def _run(self, tmp_path, monkeypatch, shutdown_set):
        from sethlans_worker_agent.unreal_executor import executor
        w = World(tmp_path, monkeypatch)
        w.flow_action = lambda job: (_ for _ in ()).throw(Cancelled('shutdown'))  # the phases saw the joined event
        shutdown, own = threading.Event(), threading.Event()
        own.set()
        if shutdown_set:
            shutdown.set()
        # The job dict World builds has no stop of its own; join this one as the claim would have.
        monkeypatch.setattr(executor, 'job_stop', lambda event, job_data: JobStop(event, own))
        return w, w.run(shutdown), shutdown

    def test_the_jobs_own_stop_releases_and_runs_finish(self, tmp_path, monkeypatch):
        w, result, shutdown = self._run(tmp_path, monkeypatch, shutdown_set=False)
        assert result.released and ('release', blender_stop.WORKER_HARDWARE_CHANGED) in w.calls
        assert ('finish', False) in w.calls and 'quarantine' not in w.names() and not shutdown.is_set()

    def test_a_worker_shutdown_still_wins(self, tmp_path, monkeypatch):
        w, result, _shutdown = self._run(tmp_path, monkeypatch, shutdown_set=True)
        assert ('release', 'shutdown') in w.calls and 'quarantine' in w.names()

    def test_a_shutdown_after_the_card_stop_skips_the_re_hash(self, tmp_path, monkeypatch):
        """Concurrency review: the shutdown budget wins even when the card stop came first."""
        from sethlans_worker_agent.unreal_executor import executor
        w = World(tmp_path, monkeypatch)
        shutdown, own = threading.Event(), threading.Event()
        own.set()
        w.flow_action = lambda job: (shutdown.set(), (_ for _ in ()).throw(Cancelled('shutdown')))[1]
        monkeypatch.setattr(executor, 'job_stop', lambda event, job_data: JobStop(event, own))
        result = w.run(shutdown)
        assert result.released and 'quarantine' in w.names() and 'finish' not in w.names()

    def test_the_claims_choice_is_forgotten_when_execute_returns(self, tmp_path, monkeypatch):
        """D16: only this claim's entry goes; another claim of the same job id keeps its own."""
        w = World(tmp_path, monkeypatch)
        kept = adapter_choice.Choice(w.params.job_id, key(1), 'GPU 1', 1)
        monkeypatch.setitem(adapter_choice._current, 'f' * 32, kept)

        def flow(job):
            assert w.choose.keywords == {'claim_id': w.reservation.claim_id}  # the flow's choices go under the claim
            adapter_choice._current[w.reservation.claim_id] = adapter_choice.Choice(w.params.job_id, key(0), 'GPU 0', 0)
            raise Cancelled('canceled')
        w.flow_action = flow
        w.run()
        assert adapter_choice.all_current() == (kept,)
