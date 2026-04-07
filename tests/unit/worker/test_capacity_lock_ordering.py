# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit test for FR-31: enforce lock ordering.

Documented order:
    WorkerCapacity._lock < _active_jobs_lock < _recent_jobs_lock

The test wraps all three locks with instrumented proxies that record
acquisition order per thread, drives poll_and_claim_job +
process_claimed_job through a mocked Blender execution, and asserts
that no inversion occurred.
"""
import threading

import pytest

from sethlans_worker_agent import job_processor
from sethlans_worker_agent.capacity import (
    CapacityProfile,
    WorkerCapacity,
)
from sethlans_worker_agent.job_lifecycle import process_claimed_job


# Declared rank: lower number acquired first.
LOCK_RANK = {
    'capacity': 0,
    'active_jobs': 1,
    'recent_jobs': 2,
}


class _InstrumentedLock:
    """Wraps a threading.Lock and records acquisition order per thread."""

    def __init__(self, label, real_lock, recorder):
        self._label = label
        self._real = real_lock
        self._recorder = recorder

    def acquire(self, *args, **kwargs):
        result = self._real.acquire(*args, **kwargs)
        if result:
            self._recorder.record_acquire(self._label)
        return result

    def release(self):
        self._recorder.record_release(self._label)
        self._real.release()

    def locked(self):
        return self._real.locked()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


class _OrderRecorder:
    """Records acquisition order per thread and detects inversions."""

    def __init__(self):
        self._held = {}  # thread_ident -> list[label]
        self._violations = []
        self._guard = threading.Lock()

    def record_acquire(self, label):
        with self._guard:
            stack = self._held.setdefault(
                threading.get_ident(), []
            )
            if stack:
                top = stack[-1]
                if LOCK_RANK[label] <= LOCK_RANK[top]:
                    self._violations.append(
                        f'inversion: holding {top} then trying to acquire {label}'
                    )
            stack.append(label)

    def record_release(self, label):
        with self._guard:
            stack = self._held.get(threading.get_ident(), [])
            if label in stack:
                stack.reverse()
                stack.remove(label)
                stack.reverse()

    @property
    def violations(self):
        return list(self._violations)


@pytest.fixture
def instrument_locks(mocker):
    recorder = _OrderRecorder()

    # Build a capacity with 1 GPU slot. WorkerCapacity._lock is reassigned
    # to the instrumented wrapper BEFORE any reservation work runs.
    profile = CapacityProfile(
        gpu_slot_count=1, cpu_slot_count=0,
        cpu_thread_ceiling=15, cpu_threads_effective=15,
        startup_gpu_count=1, gpu_mode='split',
    )
    cap = WorkerCapacity(profile)

    real_cap_lock = cap._lock
    cap._lock = _InstrumentedLock('capacity', real_cap_lock, recorder)

    real_active = job_processor._active_jobs_lock
    real_recent = job_processor._recent_jobs_lock
    mocker.patch.object(
        job_processor, '_active_jobs_lock',
        _InstrumentedLock('active_jobs', real_active, recorder),
    )
    mocker.patch.object(
        job_processor, '_recent_jobs_lock',
        _InstrumentedLock('recent_jobs', real_recent, recorder),
    )

    job_processor._capacity = cap
    yield recorder, cap


class TestLockOrdering:

    def test_poll_and_claim_job_respects_order(self, mocker, instrument_locks):
        recorder, cap = instrument_locks
        mocker.patch(
            'sethlans_worker_agent.job_processor._build_poll_params',
            return_value={},
        )
        mocker.patch(
            'sethlans_worker_agent.api_handler.poll_for_available_jobs',
            return_value=[{
                'id': 101, 'name': 'A', 'render_device': 'GPU',
                'render_engine': 'CYCLES',
            }],
        )
        mocker.patch(
            'sethlans_worker_agent.api_handler.claim_job', return_value=True,
        )

        result = job_processor.poll_and_claim_job(worker_id=1)
        assert result is not None
        assert recorder.violations == []

    def test_process_claimed_job_respects_order(self, mocker, instrument_locks):
        recorder, cap = instrument_locks
        # Reserve so the release in process_claimed_job has something to
        # release, mirroring the real lifecycle.
        reservation = cap.reserve_for_job(101, 'GPU')
        assert reservation is not None

        # Seed _active_jobs so process_claimed_job's pop() path runs.
        with job_processor._active_jobs_lock:
            job_processor._active_jobs[101] = {'job_id': 101, 'name': 'A'}

        mocker.patch(
            'sethlans_worker_agent.blender_executor.execute_blender_job',
            return_value=(True, False, 'Time: 1.23s', '', None, None, None),
        )
        mocker.patch(
            'sethlans_worker_agent.api_handler.update_job_status'
        )

        job_data = {
            'id': 101, 'name': 'A',
            'render_device': 'GPU',
            '_reservation': reservation,
            'assigned_gpu_index': reservation.primary_gpu_index,
        }

        process_claimed_job(job_data)
        assert recorder.violations == []

    def test_full_poll_then_process_sequence(self, mocker, instrument_locks):
        recorder, cap = instrument_locks
        mocker.patch(
            'sethlans_worker_agent.job_processor._build_poll_params',
            return_value={},
        )
        mocker.patch(
            'sethlans_worker_agent.api_handler.poll_for_available_jobs',
            return_value=[{
                'id': 202, 'name': 'B', 'render_device': 'GPU',
                'render_engine': 'CYCLES',
            }],
        )
        mocker.patch(
            'sethlans_worker_agent.api_handler.claim_job', return_value=True,
        )
        mocker.patch(
            'sethlans_worker_agent.blender_executor.execute_blender_job',
            return_value=(True, False, 'Time: 0.5s', '', None, None, None),
        )
        mocker.patch(
            'sethlans_worker_agent.api_handler.update_job_status'
        )

        job_data = job_processor.poll_and_claim_job(worker_id=1)
        assert job_data is not None
        process_claimed_job(job_data)
        assert recorder.violations == []
