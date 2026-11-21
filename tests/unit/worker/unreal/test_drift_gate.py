# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Card check and Unreal holds (Unreal foundation FR-WX-14; FR-T-5 "GPU drift"; render per card D15).

The check never enumerates GPUs through Blender while an Unreal job holds
a card. A lost card no longer ends the worker or sets the
shutdown event (FR-CL-6): it stops only the Blender jobs on that card.
"""
from sethlans_worker_agent import agent_shutdown, blender_stop, job_processor
from sethlans_worker_agent import capacity as capacity_module
from sethlans_worker_agent import hardware_detection

from .._card_check_env import CardEnv
from ..executors._fakes import blender_job, install_capacity, key


def _drift_spawn_spy(mocker, monkeypatch, tmp_path):
    """Spy on the detect_gpus.py subprocess the check would run."""
    exe = tmp_path / 'blender.exe'
    exe.write_text('')
    monkeypatch.setattr(hardware_detection, '_gpu_details_blender_exe', str(exe))
    return mocker.patch(
        'sethlans_worker_agent.capacity.drift.subprocess.run',
        side_effect=AssertionError('drift subprocess must not run'),
    )


class TestDriftSkippedDuringExclusiveHold:

    def test_no_subprocess_while_held_then_runs_after_release(self, mocker, monkeypatch, tmp_path):
        machine = CardEnv(monkeypatch, count=1)
        cap = install_capacity(gpu_slot_count=1, cpu_slot_count=1)
        mocker.patch('sethlans_worker_agent.config.HEARTBEAT_INTERVAL_SECONDS', 30)
        job_processor._last_drift_check_ts = 0.0
        spawn = _drift_spawn_spy(mocker, monkeypatch, tmp_path)
        cap.reserve_unreal_card(5, key(0))

        job_processor.maybe_assert_gpu_list_unchanged()
        spawn.assert_not_called()
        assert machine.detections == 0

        cap.release_slot(5)
        job_processor.maybe_assert_gpu_list_unchanged()
        assert machine.detections == 1

    def test_recheck_request_waits_for_the_hold_to_end(self, mocker, monkeypatch, tmp_path):
        """#239: a failed device check's re-check never runs under a hold."""
        machine = CardEnv(monkeypatch, count=1)
        cap = install_capacity(gpu_slot_count=1, cpu_slot_count=1)
        mocker.patch('sethlans_worker_agent.config.HEARTBEAT_INTERVAL_SECONDS', 30)
        mocker.patch('sethlans_worker_agent.job_processor.time.monotonic', return_value=1000.0)
        job_processor._last_drift_check_ts = 1000.0  # interval not due
        _drift_spawn_spy(mocker, monkeypatch, tmp_path)
        cap.reserve_unreal_card(5, key(0))
        capacity_module.request_gpu_list_recheck()

        job_processor.maybe_assert_gpu_list_unchanged()
        assert machine.detections == 0

        cap.release_slot(5)
        job_processor.maybe_assert_gpu_list_unchanged()
        assert machine.detections == 1

    def test_blender_reservation_does_not_skip_the_check(self, monkeypatch):
        machine = CardEnv(monkeypatch, count=1)
        cap = install_capacity(gpu_slot_count=1, cpu_slot_count=1)
        cap.reserve_for_job(6, 'GPU')
        job_processor._last_drift_check_ts = 0.0
        job_processor.maybe_assert_gpu_list_unchanged()
        assert machine.detections == 1


class TestLostCardDuringJobs:
    """#246 replaced by FR-CL-5: Blender jobs on the lost card stop on their own event; no status is sent."""

    def test_lost_card_stops_its_blender_job_only_and_sends_nothing(self, mocker, monkeypatch):
        update = mocker.patch('sethlans_worker_agent.api_handler.update_job_status')
        release = mocker.patch('sethlans_worker_agent.api_extras.release_job')
        machine = CardEnv(monkeypatch, count=2)
        cap = install_capacity(gpu_slot_count=2, cpu_slot_count=1)
        jobs = {job_id: blender_job(job_id) for job_id in (1, 3)}
        for job_id, job in jobs.items():
            cap.reserve_for_job(job_id, 'GPU')
            blender_stop.register_card_stop(job)
        machine.lose(1)
        job_processor._last_drift_check_ts = 0.0

        job_processor.maybe_assert_gpu_list_unchanged()

        update.assert_not_called()
        release.assert_not_called()
        assert cap.gpu_assignments_snapshot() == {key(0): 1, key(1): 3}
        assert [job_id for job_id, job in jobs.items() if job[blender_stop.CARD_STOP_KEY].is_set()] == [3]
        assert agent_shutdown._shutdown_event.is_set() is False

    def test_unreal_job_without_a_stop_event_is_not_stopped(self, monkeypatch):
        """``request_card_stop`` does nothing for an id with no registered Blender stop."""
        CardEnv(monkeypatch, count=1)
        assert blender_stop.request_card_stop(2) is False
        assert blender_stop.request_card_stop(-3) is False
