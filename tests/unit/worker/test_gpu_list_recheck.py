# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
GPU list comparison and the immediate re-check after a failed GPU device
check (#239).

A render whose GPU device check failed (#238) asks, through a
threading.Event, for one GPU list comparison on the next agent-loop
iteration; afterwards the heartbeat interval applies again.
"""
import threading
from types import SimpleNamespace

import pytest

from sethlans_worker_agent import capacity as capacity_module
from sethlans_worker_agent import blender_failure, job_processor
from sethlans_worker_agent.capacity import CapacityProfile, WorkerCapacity
from sethlans_worker_agent.capacity.gpu_list import describe_gpu_list_change, gpu_identity
from sethlans_worker_agent.gpu_device_script import GPU_CHECK_EXIT_CODE, GPU_CHECK_MARKER

MARKER_STDERR = f"{GPU_CHECK_MARKER} 0 Blender device entries match OPTIX 'x'\n"


class TestGpuIdentity:

    def test_ordered_id_type_pairs(self):
        records = [{'id': 'b', 'type': 'HIP', 'name': 'B'}, {'id': 'a', 'type': 'CUDA'}]
        assert gpu_identity(records) == [('b', 'HIP'), ('a', 'CUDA')]

    def test_empty_and_none(self):
        assert gpu_identity([]) == []
        assert gpu_identity(None) == []


class TestDescribeGpuListChange:

    def test_equal_lists_give_none(self):
        assert describe_gpu_list_change([('a', 'HIP')], [('a', 'HIP')]) is None
        assert describe_gpu_list_change([], []) is None

    def test_gone_and_new_in_the_same_interval(self):
        change = describe_gpu_list_change([('a', 'HIP'), ('b', 'HIP')], [('a', 'HIP'), ('c', 'HIP')])
        assert "cards gone: [HIP 'b']" in change
        assert "cards new: [HIP 'c']" in change
        assert 'order changed' not in change

    def test_optix_and_cuda_entries_are_the_same_card(self):
        change = describe_gpu_list_change([('X_OptiX', 'OPTIX')], [('X', 'CUDA')])
        assert change.startswith("backend or id changed: [OPTIX 'X_OptiX' -> CUDA 'X']")
        assert 'gone' not in change and 'new' not in change

    def test_reorder_is_named(self):
        change = describe_gpu_list_change([('a', 'HIP'), ('b', 'CUDA')], [('b', 'CUDA'), ('a', 'HIP')])
        assert change.startswith('order changed')


class TestRecheckRequest:

    def test_pending_until_taken_and_requests_coalesce(self):
        capacity_module.request_gpu_list_recheck()
        capacity_module.request_gpu_list_recheck()
        assert capacity_module.gpu_list_recheck_pending() is True
        assert capacity_module.gpu_list_recheck_pending() is True  # not cleared by looking

        assert capacity_module.take_gpu_list_check_turn(1000.0, periodic_due=False) is True
        assert capacity_module.gpu_list_recheck_pending() is False

    @pytest.mark.parametrize('code, stderr, expected', [
        (GPU_CHECK_EXIT_CODE, MARKER_STDERR, True),
        (120, MARKER_STDERR, True),
        (GPU_CHECK_EXIT_CODE, 'segfault\n', False),
        (1, 'Error: boom\n', False),
        (0, MARKER_STDERR, False),
        (86, MARKER_STDERR, False),  # exit 86 is always a settings failure (#240)
    ])
    def test_only_a_failed_device_check_requests(self, code, stderr, expected):
        assert blender_failure.request_recheck_if_check_failed(code, stderr) is expected
        assert capacity_module.gpu_list_recheck_pending() is expected

    def test_request_from_another_thread_is_seen(self):
        t = threading.Thread(
            target=blender_failure.request_recheck_if_check_failed,
            args=(GPU_CHECK_EXIT_CODE, MARKER_STDERR),
        )
        t.start()
        t.join()
        assert capacity_module.gpu_list_recheck_pending() is True


def _capacity(gpu_slot_count=1, startup_gpu_count=1):
    return WorkerCapacity(CapacityProfile(
        gpu_slot_count=gpu_slot_count, cpu_slot_count=1, cpu_thread_ceiling=15,
        cpu_threads_effective=15, startup_gpu_count=startup_gpu_count, gpu_mode='split',
    ))


@pytest.fixture
def loop(mocker):
    """A one-GPU worker that just compared; real comparison, counted detections."""
    job_processor._capacity = _capacity()
    mocker.patch(
        'sethlans_worker_agent.system_monitor.get_gpu_device_details',
        return_value=[{'id': 'CUDA_GPU_0', 'type': 'CUDA', 'name': 'GPU 0'}],
    )
    detect = mocker.patch(
        'sethlans_worker_agent.capacity.slots.detect_physical_gpus_now',
        return_value=[('CUDA_GPU_0', 'CUDA')],
    )
    interval = mocker.patch('sethlans_worker_agent.config.HEARTBEAT_INTERVAL_SECONDS', 30)
    clock = SimpleNamespace(now=1000.0)
    mocker.patch('sethlans_worker_agent.job_processor.time.monotonic', side_effect=lambda: clock.now)
    job_processor._last_drift_check_ts = 1000.0
    return SimpleNamespace(clock=clock, detect=detect, interval=interval, mocker=mocker)


def _tick(loop, now, request=False):
    loop.clock.now = now
    if request:
        blender_failure.request_recheck_if_check_failed(GPU_CHECK_EXIT_CODE, MARKER_STDERR)
    job_processor.maybe_assert_gpu_list_unchanged()
    return loop.detect.call_count


class TestImmediateRecheck:

    def test_first_request_runs_at_once_then_interval_applies(self, loop):
        assert _tick(loop, 1005.0) == 0  # not due, nothing requested
        assert _tick(loop, 1006.0, request=True) == 1  # immediate comparison
        assert job_processor._last_drift_check_ts == 1006.0
        for now in (1007.0, 1020.0, 1035.0):
            assert _tick(loop, now) == 1  # no tight loop when nothing changed
        assert _tick(loop, 1036.0) == 2  # interval, counted from the immediate run

    def test_floor_gives_one_detection_then_the_next_after_it(self, loop, mocker):
        mocker.patch('sethlans_worker_agent.config.HEARTBEAT_INTERVAL_SECONDS', 3600)
        floor = capacity_module.IMMEDIATE_RECHECK_FLOOR_SECONDS
        assert floor == 300
        assert _tick(loop, 1001.0, request=True) == 1
        for now in (1002.0, 1100.0, 1200.0, 1001.0 + floor - 1):
            assert _tick(loop, now, request=True) == 1
            assert capacity_module.gpu_list_recheck_pending() is True  # kept, not dropped
        assert _tick(loop, 1001.0 + floor) == 2  # floor expired: the pending request runs
        assert capacity_module.gpu_list_recheck_pending() is False

    def test_periodic_run_answers_a_pending_request(self, loop):
        assert _tick(loop, 1001.0, request=True) == 1
        assert _tick(loop, 1002.0, request=True) == 1  # inside the floor: pending
        assert capacity_module.gpu_list_recheck_pending() is True
        assert _tick(loop, 1031.0) == 2  # periodic run is due
        assert capacity_module.gpu_list_recheck_pending() is False

    def test_comparison_that_raises_leaves_the_request_pending(self, loop):
        loop.detect.side_effect = RuntimeError('transient')
        loop.clock.now = 1001.0
        capacity_module.request_gpu_list_recheck()

        with pytest.raises(RuntimeError):
            job_processor.maybe_assert_gpu_list_unchanged()

        assert capacity_module.gpu_list_recheck_pending() is True
        assert job_processor._last_drift_check_ts == 1000.0

    def test_unreal_gate_defers_the_request_without_losing_it(self, loop, mocker):
        allowed = mocker.patch('sethlans_worker_agent.unreal_bridge.drift_check_allowed', return_value=False)
        assert _tick(loop, 1001.0, request=True) == 0
        assert capacity_module.gpu_list_recheck_pending() is True

        allowed.return_value = True
        assert _tick(loop, 1002.0) == 1


class TestFailedDetection:
    """A detection that failed ("try again later") does not use up a request (#253)."""

    def test_request_stays_pending_and_the_floor_still_applies(self, loop, mocker):
        mocker.patch('sethlans_worker_agent.config.HEARTBEAT_INTERVAL_SECONDS', 3600)
        floor = capacity_module.IMMEDIATE_RECHECK_FLOOR_SECONDS
        loop.detect.return_value = None

        assert _tick(loop, 1001.0, request=True) == 1
        assert capacity_module.gpu_list_recheck_pending() is True  # put back
        for now in (1002.0, 1100.0, 1001.0 + floor - 1):
            assert _tick(loop, now) == 1  # no retry inside the floor
        assert _tick(loop, 1001.0 + floor) == 2  # the floor expired: retried
        assert capacity_module.gpu_list_recheck_pending() is True

        loop.detect.return_value = [('CUDA_GPU_0', 'CUDA')]
        assert _tick(loop, 1001.0 + 2 * floor) == 3
        assert capacity_module.gpu_list_recheck_pending() is False

    def test_periodic_run_that_answered_a_request_puts_it_back(self, loop):
        assert _tick(loop, 1001.0, request=True) == 1
        assert _tick(loop, 1002.0, request=True) == 1  # inside the floor: pending
        loop.detect.return_value = None

        assert _tick(loop, 1031.0) == 2  # periodic run answers it, detection fails
        assert capacity_module.gpu_list_recheck_pending() is True
        assert _tick(loop, 1032.0) == 2  # still inside the floor of the 1001 run

    def test_periodic_failure_without_a_request_creates_none(self, loop):
        loop.detect.return_value = None

        assert _tick(loop, 1030.0) == 1
        assert capacity_module.gpu_list_recheck_pending() is False
        assert _tick(loop, 1031.0) == 1
        assert job_processor._last_drift_check_ts == 1030.0


class TestWorkersThatSkipTheComparison:

    @pytest.mark.parametrize('gpu_slots, startup_gpus', [(0, 0), (0, 2)],
                             ids=['no-gpu', 'force-cpu-only'])
    def test_never_detects_and_absorbs_requests(self, loop, gpu_slots, startup_gpus):
        job_processor._capacity = _capacity(gpu_slot_count=gpu_slots, startup_gpu_count=startup_gpus)

        assert _tick(loop, 1001.0, request=True) == 0
        assert capacity_module.gpu_list_recheck_pending() is False
        assert _tick(loop, 5000.0) == 0  # periodic run due: still no detection
        assert job_processor._last_drift_check_ts == 5000.0

    def test_force_gpu_index_worker_still_compares(self, loop):
        cap = WorkerCapacity(CapacityProfile(
            gpu_slot_count=1, cpu_slot_count=1, cpu_thread_ceiling=15, cpu_threads_effective=15,
            startup_gpu_count=1, gpu_mode='split', force_gpu_index=0,
        ))
        job_processor._capacity = cap
        assert _tick(loop, 1031.0) == 1
