# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the capacity-aware pieces of job_processor.

Split from test_job_processor.py to keep each file under the 300-line
Python limit. Covers init_capacity / get_worker_capacity,
capacity_is_full, poll_and_claim_job slot release on failure and the
maybe_assert_gpu_list_unchanged cadence (with the rebuild step before it).
The drift-only termination helper is gone (FR-CL-6; test_capacity_drift.py).
"""
import pytest

from sethlans_worker_agent import job_processor
from sethlans_worker_agent.capacity import WorkerCapacity

from .executors._fakes import key, profile as _build_profile


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
        mocker.patch('sethlans_worker_agent.config.CPU_THREADS', 0)

        cap = job_processor.init_capacity()
        assert isinstance(cap, WorkerCapacity)
        assert cap.total == 1  # no saved card choice: method cpu, the CPU slot only
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
        assert cap.gpu_assignments_snapshot() == {key(0): 101}


# --- maybe_assert_gpu_list_unchanged cadence ---

class TestMaybeAssertGpuListUnchanged:

    def test_no_op_before_init(self):
        job_processor._capacity = None
        # Should simply return without raising.
        job_processor.maybe_assert_gpu_list_unchanged()

    def test_fires_once_per_heartbeat_interval(self, mocker):
        cap = WorkerCapacity(_build_profile())
        job_processor._capacity = cap
        mocker.patch(
            'sethlans_worker_agent.config.HEARTBEAT_INTERVAL_SECONDS', 30,
        )
        spy = mocker.patch.object(cap, 'assert_gpu_list_unchanged')
        # Three clock reads: first fires, second skipped, third fires.
        monotonic = mocker.patch(
            'sethlans_worker_agent.job_processor.time.monotonic',
            side_effect=[1000.0, 1005.0, 1031.0],
        )
        job_processor._last_drift_check_ts = 0.0

        job_processor.maybe_assert_gpu_list_unchanged()  # 1000
        job_processor.maybe_assert_gpu_list_unchanged()  # 1005 (skipped)
        job_processor.maybe_assert_gpu_list_unchanged()  # 1031 (fires)
        assert spy.call_count == 2
        assert monotonic.call_count == 3

    def test_timestamp_not_updated_when_assert_raises(self, mocker):
        """Issue #49: if assert_gpu_list_unchanged raises, the cadence
        timestamp must NOT be advanced, so the next loop iteration will
        retry immediately rather than waiting a full heartbeat interval.
        """
        cap = WorkerCapacity(_build_profile())
        job_processor._capacity = cap
        mocker.patch(
            'sethlans_worker_agent.config.HEARTBEAT_INTERVAL_SECONDS', 30,
        )
        mocker.patch.object(
            cap, 'assert_gpu_list_unchanged',
            side_effect=RuntimeError('transient'),
        )
        mocker.patch(
            'sethlans_worker_agent.job_processor.time.monotonic',
            return_value=5000.0,
        )
        job_processor._last_drift_check_ts = 1000.0

        with pytest.raises(RuntimeError):
            job_processor.maybe_assert_gpu_list_unchanged()

        # Timestamp must remain at its prior value.
        assert job_processor._last_drift_check_ts == 1000.0

    def test_rebuild_step_runs_on_every_call_before_the_rate_limit(self, mocker):
        """FR-CL-4: also when the check is not due and when the Unreal gate defers it."""
        cap = WorkerCapacity(_build_profile())
        job_processor._capacity = cap
        step = mocker.patch('sethlans_worker_agent.capacity.rebuild.step')
        check = mocker.patch.object(cap, 'assert_gpu_list_unchanged')
        mocker.patch('sethlans_worker_agent.config.HEARTBEAT_INTERVAL_SECONDS', 30)
        mocker.patch('sethlans_worker_agent.job_processor.time.monotonic', return_value=1005.0)
        job_processor._last_drift_check_ts = 1000.0
        gate = mocker.patch('sethlans_worker_agent.unreal_bridge.drift_check_allowed', return_value=True)

        job_processor.maybe_assert_gpu_list_unchanged()  # not due
        gate.return_value = False
        job_processor.maybe_assert_gpu_list_unchanged()  # Unreal render

        assert [c.args for c in step.call_args_list] == [(cap,), (cap,)]
        check.assert_not_called()
