# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
GPU drift check and exclusive holds (Unreal foundation FR-WX-14;
FR-T-5 "GPU drift").
"""
import threading

from sethlans_worker_agent import agent, job_processor, job_resources
from sethlans_worker_agent import capacity as capacity_module
from sethlans_worker_agent import hardware_detection

from ..executors._fakes import install_capacity


def _drift_spawn_spy(mocker, monkeypatch, tmp_path):
    """Spy on the detect_gpus.py subprocess the drift check would run."""
    exe = tmp_path / 'blender.exe'
    exe.write_text('')
    monkeypatch.setattr(hardware_detection, '_gpu_details_blender_exe', str(exe))
    return mocker.patch(
        'sethlans_worker_agent.capacity.drift.subprocess.run',
        side_effect=AssertionError('drift subprocess must not run'),
    )


def _same_gpu_list(mocker):
    """Detection finds the one card in use; return the detection spy."""
    mocker.patch(
        'sethlans_worker_agent.system_monitor.get_gpu_device_details',
        return_value=[{'id': 'CUDA_GPU_0', 'type': 'CUDA', 'name': 'GPU 0'}],
    )
    return mocker.patch(
        'sethlans_worker_agent.capacity.slots.detect_physical_gpus_now',
        return_value=[('CUDA_GPU_0', 'CUDA')],
    )


class TestDriftSkippedDuringExclusiveHold:

    def test_no_subprocess_while_held_then_runs_after_release(self, mocker, monkeypatch, tmp_path):
        cap = install_capacity(gpu_slot_count=1, cpu_slot_count=1)
        mocker.patch('sethlans_worker_agent.config.HEARTBEAT_INTERVAL_SECONDS', 30)
        job_processor._last_drift_check_ts = 0.0
        spawn = _drift_spawn_spy(mocker, monkeypatch, tmp_path)
        cap.reserve_exclusive(5)

        job_processor.maybe_assert_gpu_list_unchanged()
        spawn.assert_not_called()

        cap.release_slot(5)
        detect = _same_gpu_list(mocker)
        job_processor.maybe_assert_gpu_list_unchanged()
        detect.assert_called_once()

    def test_recheck_request_waits_for_the_hold_to_end(self, mocker, monkeypatch, tmp_path):
        """#239: a failed device check's re-check never runs under a hold."""
        cap = install_capacity(gpu_slot_count=1, cpu_slot_count=1)
        mocker.patch('sethlans_worker_agent.config.HEARTBEAT_INTERVAL_SECONDS', 30)
        mocker.patch('sethlans_worker_agent.job_processor.time.monotonic', return_value=1000.0)
        job_processor._last_drift_check_ts = 1000.0  # interval not due
        spawn = _drift_spawn_spy(mocker, monkeypatch, tmp_path)
        cap.reserve_exclusive(5)
        capacity_module.request_gpu_list_recheck()

        job_processor.maybe_assert_gpu_list_unchanged()
        spawn.assert_not_called()

        cap.release_slot(5)
        detect = _same_gpu_list(mocker)
        job_processor.maybe_assert_gpu_list_unchanged()
        detect.assert_called_once()

    def test_blender_reservation_does_not_skip_drift(self, mocker):
        cap = install_capacity(gpu_slot_count=1, cpu_slot_count=1)
        cap.reserve_for_job(6, 'GPU')
        job_processor._last_drift_check_ts = 0.0
        detect = _same_gpu_list(mocker)
        job_processor.maybe_assert_gpu_list_unchanged()
        detect.assert_called_once()


class TestDriftHandler:
    """#246: Blender jobs are stopped through the shutdown event, not CANCELED."""

    def test_mixed_jobs_send_nothing_and_set_the_shutdown_event(self, mocker):
        update = mocker.patch('sethlans_worker_agent.api_handler.update_job_status')
        release = mocker.patch('sethlans_worker_agent.api_extras.release_job')
        stop = threading.Event()
        mocker.patch.object(agent, '_shutdown_event', stop)
        stop_spy = mocker.spy(job_resources, 'request_shutdown_for_stop')
        release_spy = mocker.spy(job_resources, 'request_shutdown_for_release')
        with job_processor._active_jobs_lock:
            job_processor._active_jobs[1] = {'job_id': 1, 'renderer': 'BLENDER'}
            job_processor._active_jobs[2] = {'job_id': 2, 'renderer': 'UNREAL'}
            job_processor._active_jobs[3] = {'job_id': 3}

        job_processor.terminate_all_active_jobs_for_drift()

        update.assert_not_called()
        release.assert_not_called()  # Unreal releases from its own executor on shutdown
        assert sorted(stop_spy.call_args.args[0]) == [1, 3]
        assert release_spy.call_args.args[0] == [2]
        assert stop.is_set()

    def test_blender_only_sets_the_shutdown_event(self, mocker):
        update = mocker.patch('sethlans_worker_agent.api_handler.update_job_status')
        stop = threading.Event()
        mocker.patch.object(agent, '_shutdown_event', stop)
        with job_processor._active_jobs_lock:
            job_processor._active_jobs[1] = {'job_id': 1, 'renderer': 'BLENDER'}

        job_processor.terminate_all_active_jobs_for_drift()

        update.assert_not_called()
        assert stop.is_set()
