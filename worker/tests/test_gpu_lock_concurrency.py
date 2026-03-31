# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
# tests/unit/worker_agent/test_gpu_lock_concurrency.py
"""
Unit tests for GPU lock concurrency safety in job_processor.

Verifies that _gpu_lock protects _gpu_assignment_map from
duplicate GPU assignments under concurrent access.
"""

import threading

from sethlans_worker_agent import job_processor


class TestGpuLockConcurrency:
    """Tests that concurrent GPU reservation does not produce duplicates."""

    def test_concurrent_reserve_no_duplicate_assignments(self, mocker):
        """
        Spawn multiple threads that all try to reserve a GPU simultaneously.
        With 2 GPUs and 10 threads, exactly 2 should succeed and each must
        get a unique GPU index. No duplicates should appear.
        """
        mocker.patch(
            'sethlans_worker_agent.system_monitor.get_gpu_device_details',
            return_value=[{}, {}]  # 2 GPUs
        )
        job_processor._gpu_assignment_map.clear()

        results = []
        barrier = threading.Barrier(10)

        def try_reserve(job_id):
            barrier.wait()  # Synchronize all threads to start together
            idx = job_processor._reserve_next_available_gpu(job_id)
            if idx is not None:
                results.append((job_id, idx))

        threads = [
            threading.Thread(target=try_reserve, args=(i,))
            for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly 2 GPUs should be assigned
        assert len(results) == 2

        # GPU indices must be unique (no duplicate assignments)
        assigned_indices = [idx for _, idx in results]
        assert len(set(assigned_indices)) == 2
        assert set(assigned_indices) == {0, 1}

        # The assignment map should have exactly 2 entries
        assert len(job_processor._gpu_assignment_map) == 2

    def test_concurrent_reserve_with_one_gpu(self, mocker):
        """
        With only 1 GPU, exactly 1 thread should succeed out of many.
        """
        mocker.patch(
            'sethlans_worker_agent.system_monitor.get_gpu_device_details',
            return_value=[{}]  # 1 GPU
        )
        job_processor._gpu_assignment_map.clear()

        results = []
        barrier = threading.Barrier(5)

        def try_reserve(job_id):
            barrier.wait()
            idx = job_processor._reserve_next_available_gpu(job_id)
            if idx is not None:
                results.append((job_id, idx))

        threads = [
            threading.Thread(target=try_reserve, args=(i,))
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 1
        assert results[0][1] == 0

    def test_concurrent_reserve_with_no_gpus(self, mocker):
        """
        With 0 GPUs, all threads should get None.
        """
        mocker.patch(
            'sethlans_worker_agent.system_monitor.get_gpu_device_details',
            return_value=[]
        )
        job_processor._gpu_assignment_map.clear()

        results = []

        def try_reserve(job_id):
            idx = job_processor._reserve_next_available_gpu(job_id)
            results.append(idx)

        threads = [
            threading.Thread(target=try_reserve, args=(i,))
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(r is None for r in results)
        assert len(job_processor._gpu_assignment_map) == 0

    def test_reserve_then_release_then_reserve(self, mocker):
        """
        After releasing a GPU, a new reservation should claim the freed slot.
        """
        mocker.patch(
            'sethlans_worker_agent.system_monitor.get_gpu_device_details',
            return_value=[{}, {}]
        )
        job_processor._gpu_assignment_map.clear()

        # Reserve both GPUs
        idx0 = job_processor._reserve_next_available_gpu(100)
        idx1 = job_processor._reserve_next_available_gpu(200)
        assert idx0 == 0
        assert idx1 == 1

        # All full
        assert job_processor._reserve_next_available_gpu(300) is None

        # Release GPU 0
        with job_processor._gpu_lock:
            job_processor._gpu_assignment_map.pop(0)

        # Now GPU 0 should be available again
        idx_new = job_processor._reserve_next_available_gpu(300)
        assert idx_new == 0
        assert job_processor._gpu_assignment_map[0] == 300
