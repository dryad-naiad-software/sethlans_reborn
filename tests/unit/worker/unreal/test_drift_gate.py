# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
GPU drift check and exclusive holds (Unreal foundation FR-WX-14;
FR-T-5 "GPU drift").
"""
import threading

from sethlans_worker_agent import agent, job_processor

from ..executors._fakes import install_capacity


def _drift_spawn_spy(mocker):
    """Spy on the detect_gpus.py subprocess the drift check would run."""
    mocker.patch(
        'sethlans_worker_agent.hardware_detection._find_any_blender_executable',
        return_value='/fake/blender',
    )
    return mocker.patch(
        'sethlans_worker_agent.capacity.drift.subprocess.run',
        side_effect=AssertionError('drift subprocess must not run'),
    )


class TestDriftSkippedDuringExclusiveHold:

    def test_no_subprocess_while_held_then_runs_after_release(self, mocker):
        cap = install_capacity(gpu_slot_count=1, cpu_slot_count=1)
        mocker.patch('sethlans_worker_agent.config.HEARTBEAT_INTERVAL_SECONDS', 30)
        job_processor._last_drift_check_ts = 0.0
        spawn = _drift_spawn_spy(mocker)
        cap.reserve_exclusive(5)

        job_processor.maybe_assert_gpu_count_unchanged()
        spawn.assert_not_called()

        cap.release_slot(5)
        count = mocker.patch(
            'sethlans_worker_agent.capacity.slots.count_physical_gpus_now',
            return_value=1,
        )
        job_processor.maybe_assert_gpu_count_unchanged()
        count.assert_called_once()

    def test_blender_reservation_does_not_skip_drift(self, mocker):
        cap = install_capacity(gpu_slot_count=1, cpu_slot_count=1)
        cap.reserve_for_job(6, 'GPU')
        job_processor._last_drift_check_ts = 0.0
        count = mocker.patch(
            'sethlans_worker_agent.capacity.slots.count_physical_gpus_now',
            return_value=1,
        )
        job_processor.maybe_assert_gpu_count_unchanged()
        count.assert_called_once()


class TestDriftHandler:

    def test_blender_cancelled_unreal_only_shutdown(self, mocker):
        update = mocker.patch('sethlans_worker_agent.api_handler.update_job_status')
        stop = threading.Event()
        mocker.patch.object(agent, '_shutdown_event', stop)
        with job_processor._active_jobs_lock:
            job_processor._active_jobs[1] = {'job_id': 1, 'renderer': 'BLENDER'}
            job_processor._active_jobs[2] = {'job_id': 2, 'renderer': 'UNREAL'}
            job_processor._active_jobs[3] = {'job_id': 3}
        job_processor.terminate_all_active_jobs_for_drift()
        assert sorted(c.args[0] for c in update.call_args_list) == [1, 3]
        assert all(c.args[1] == {'status': 'CANCELED'} for c in update.call_args_list)
        assert stop.is_set()

    def test_blender_only_does_not_touch_shutdown(self, mocker):
        mocker.patch('sethlans_worker_agent.api_handler.update_job_status')
        stop = threading.Event()
        mocker.patch.object(agent, '_shutdown_event', stop)
        with job_processor._active_jobs_lock:
            job_processor._active_jobs[1] = {'job_id': 1, 'renderer': 'BLENDER'}
        job_processor.terminate_all_active_jobs_for_drift()
        assert not stop.is_set()
