# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the capacity-aware pieces of job_processor.

Split from test_job_processor.py to keep each file under the 300-line
Python limit. Covers init_capacity / get_worker_capacity,
capacity_is_full, poll_and_claim_job slot release on failure,
maybe_assert_gpu_count_unchanged cadence, and the
terminate_all_active_jobs_for_drift helper.
"""
import pytest

from sethlans_worker_agent import job_processor
from sethlans_worker_agent.capacity import (
    CapacityProfile,
    WorkerCapacity,
)


def _build_profile(gpu_slot_count=1, cpu_slot_count=1, startup_gpu_count=1,
                   gpu_mode='split', force_gpu_index=None,
                   cpu_thread_ceiling=15, cpu_threads_effective=15):
    return CapacityProfile(
        gpu_slot_count=gpu_slot_count,
        cpu_slot_count=cpu_slot_count,
        cpu_thread_ceiling=cpu_thread_ceiling,
        cpu_threads_effective=cpu_threads_effective,
        startup_gpu_count=startup_gpu_count,
        gpu_mode=gpu_mode,
        force_gpu_index=force_gpu_index,
    )


# --- init_capacity / get_worker_capacity ---

class TestInitCapacity:

    def test_init_capacity_builds_worker_capacity(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.system_monitor.get_gpu_device_details',
            return_value=[{'name': 'GPU0'}, {'name': 'GPU1'}],
        )
        mocker.patch(
            'sethlans_worker_agent.system_monitor.get_cpu_thread_count',
            return_value=16,
        )
        mocker.patch('sethlans_worker_agent.config.FORCE_CPU_ONLY', False)
        mocker.patch('sethlans_worker_agent.config.FORCE_GPU_ONLY', False)
        mocker.patch('sethlans_worker_agent.config.FORCE_GPU_INDEX', None)
        mocker.patch('sethlans_worker_agent.config.GPU_MODE', 'split')
        mocker.patch('sethlans_worker_agent.config.CPU_THREADS', 0)

        cap = job_processor.init_capacity()
        assert isinstance(cap, WorkerCapacity)
        assert cap.total == 3  # 2 gpu + 1 cpu
        assert job_processor.get_worker_capacity() is cap

    def test_capacity_is_full_false_before_init(self):
        job_processor._capacity = None
        assert job_processor.capacity_is_full() is False

    def test_capacity_is_full_delegates_when_initialized(self):
        cap = WorkerCapacity(_build_profile(gpu_slot_count=0, cpu_slot_count=1))
        job_processor._capacity = cap
        assert job_processor.capacity_is_full() is False
        cap.reserve_for_job(42, 'CPU')
        assert job_processor.capacity_is_full() is True


# --- poll_and_claim_job slot release on failure ---

class TestPollAndClaimJobSlotRelease:

    def _setup(self, mocker, claim_return=None, claim_exception=None):
        cap = WorkerCapacity(_build_profile(gpu_slot_count=1, cpu_slot_count=0))
        job_processor._capacity = cap
        mocker.patch(
            'sethlans_worker_agent.job_processor._build_poll_params',
            return_value={},
        )
        mocker.patch(
            'sethlans_worker_agent.api_handler.poll_for_available_jobs',
            return_value=[{
                'id': 101, 'name': 'Job A', 'render_device': 'GPU',
                'render_engine': 'CYCLES',
            }],
        )
        if claim_exception is not None:
            mocker.patch(
                'sethlans_worker_agent.api_handler.claim_job',
                side_effect=claim_exception,
            )
        else:
            mocker.patch(
                'sethlans_worker_agent.api_handler.claim_job',
                return_value=claim_return,
            )
        return cap

    def test_slot_released_on_claim_returning_false(self, mocker):
        cap = self._setup(mocker, claim_return=False)
        result = job_processor.poll_and_claim_job(worker_id=1)
        assert result is None
        assert cap.gpu_assignments_snapshot() == {}
        assert cap.is_full() is False

    def test_slot_released_on_claim_exception(self, mocker):
        cap = self._setup(mocker, claim_exception=RuntimeError('boom'))
        with pytest.raises(RuntimeError):
            job_processor.poll_and_claim_job(worker_id=1)
        assert cap.gpu_assignments_snapshot() == {}

    def test_slot_released_on_claim_base_exception(self, mocker):
        cap = self._setup(mocker, claim_exception=SystemExit(1))
        with pytest.raises(SystemExit):
            job_processor.poll_and_claim_job(worker_id=1)
        assert cap.gpu_assignments_snapshot() == {}

    def test_slot_retained_on_successful_claim(self, mocker):
        cap = self._setup(mocker, claim_return=True)
        result = job_processor.poll_and_claim_job(worker_id=1)
        assert result is not None
        assert result['id'] == 101
        assert cap.gpu_assignments_snapshot() == {0: 101}


# --- maybe_assert_gpu_count_unchanged cadence ---

class TestMaybeAssertGpuCountUnchanged:

    def test_no_op_before_init(self):
        job_processor._capacity = None
        # Should simply return without raising.
        job_processor.maybe_assert_gpu_count_unchanged()

    def test_fires_once_per_heartbeat_interval(self, mocker):
        cap = WorkerCapacity(_build_profile())
        job_processor._capacity = cap
        mocker.patch(
            'sethlans_worker_agent.config.HEARTBEAT_INTERVAL_SECONDS', 30,
        )
        spy = mocker.patch.object(cap, 'assert_gpu_count_unchanged')
        # Three clock reads: first fires, second skipped, third fires.
        monotonic = mocker.patch(
            'sethlans_worker_agent.job_processor.time.monotonic',
            side_effect=[1000.0, 1005.0, 1031.0],
        )
        job_processor._last_drift_check_ts = 0.0

        job_processor.maybe_assert_gpu_count_unchanged()  # 1000
        job_processor.maybe_assert_gpu_count_unchanged()  # 1005 (skipped)
        job_processor.maybe_assert_gpu_count_unchanged()  # 1031 (fires)
        assert spy.call_count == 2
        assert monotonic.call_count == 3


# --- terminate_all_active_jobs_for_drift ---

class TestTerminateAllActiveJobsForDrift:

    def test_no_active_jobs_is_noop(self, mocker):
        spy = mocker.patch(
            'sethlans_worker_agent.api_handler.update_job_status'
        )
        job_processor.terminate_all_active_jobs_for_drift()
        spy.assert_not_called()

    def test_cancels_each_active_job(self, mocker):
        spy = mocker.patch(
            'sethlans_worker_agent.api_handler.update_job_status'
        )
        with job_processor._active_jobs_lock:
            job_processor._active_jobs[1] = {'job_id': 1}
            job_processor._active_jobs[7] = {'job_id': 7}
        job_processor.terminate_all_active_jobs_for_drift()
        assert spy.call_count == 2
        called_ids = sorted(call.args[0] for call in spy.call_args_list)
        assert called_ids == [1, 7]
        for call in spy.call_args_list:
            assert call.args[1] == {'status': 'CANCELED'}

    def test_continues_on_cancel_error(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.api_handler.update_job_status',
            side_effect=[RuntimeError('net'), None],
        )
        with job_processor._active_jobs_lock:
            job_processor._active_jobs[1] = {'job_id': 1}
            job_processor._active_jobs[2] = {'job_id': 2}
        # Must not raise.
        job_processor.terminate_all_active_jobs_for_drift()
