# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Integration tests for GPU lock concurrency in job_processor.

Uses real threads with short timeouts to verify thread safety of
GPU reservation and release under concurrent access.
"""

import threading

from sethlans_worker_agent import job_processor


def _mock_gpus(mocker, count):
    """Patch system_monitor.get_gpu_device_details to return N GPUs."""
    gpu_list = [
        {'name': f'GPU-{i}', 'type': 'CUDA', 'index': i}
        for i in range(count)
    ]
    mocker.patch(
        'sethlans_worker_agent.job_processor.system_monitor'
        '.get_gpu_device_details',
        return_value=gpu_list,
    )
    return gpu_list


# -- Multiple threads reserving GPUs --

def test_only_one_thread_per_gpu(mocker):
    """Each GPU can only be reserved by one thread at a time."""
    _mock_gpus(mocker, 2)

    results = {}
    barrier = threading.Barrier(2, timeout=5)

    def reserve_gpu(thread_id):
        barrier.wait()
        idx = job_processor._reserve_next_available_gpu(
            job_id=thread_id
        )
        results[thread_id] = idx

    t1 = threading.Thread(target=reserve_gpu, args=(100,))
    t2 = threading.Thread(target=reserve_gpu, args=(200,))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    # Both should succeed but with different GPU indices
    assert results[100] is not None
    assert results[200] is not None
    assert results[100] != results[200]
    assert {results[100], results[200]} == {0, 1}


def test_gpu_release_allows_next_thread(mocker):
    """After releasing a GPU, another thread can acquire it."""
    _mock_gpus(mocker, 1)

    # First thread reserves the only GPU
    idx1 = job_processor._reserve_next_available_gpu(job_id=100)
    assert idx1 == 0

    # Second thread cannot acquire
    idx2 = job_processor._reserve_next_available_gpu(job_id=200)
    assert idx2 is None

    # Release GPU from first thread
    with job_processor._gpu_lock:
        job_processor._gpu_assignment_map.pop(idx1, None)

    # Now second thread can acquire
    idx3 = job_processor._reserve_next_available_gpu(job_id=300)
    assert idx3 == 0


def test_all_gpus_busy_returns_none(mocker):
    """When all GPUs are busy, reservation returns None."""
    _mock_gpus(mocker, 2)

    idx1 = job_processor._reserve_next_available_gpu(job_id=100)
    idx2 = job_processor._reserve_next_available_gpu(job_id=200)
    assert idx1 is not None
    assert idx2 is not None

    # All GPUs taken
    idx3 = job_processor._reserve_next_available_gpu(job_id=300)
    assert idx3 is None


def test_no_gpus_returns_none(mocker):
    """When no GPUs exist, reservation returns None."""
    _mock_gpus(mocker, 0)

    idx = job_processor._reserve_next_available_gpu(job_id=100)
    assert idx is None


# -- Thread safety under concurrent access --

def test_concurrent_gpu_reservations_no_duplicates(mocker):
    """Many threads competing for GPUs never get duplicate assignments."""
    num_gpus = 4
    num_threads = 20
    _mock_gpus(mocker, num_gpus)

    results = []
    results_lock = threading.Lock()
    barrier = threading.Barrier(num_threads, timeout=10)

    def reserve_gpu(job_id):
        barrier.wait()
        idx = job_processor._reserve_next_available_gpu(
            job_id=job_id
        )
        with results_lock:
            results.append((job_id, idx))

    threads = [
        threading.Thread(target=reserve_gpu, args=(i,))
        for i in range(num_threads)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    # Collect successful reservations
    successful = [
        (job_id, idx) for job_id, idx in results if idx is not None
    ]
    failed = [
        (job_id, idx) for job_id, idx in results if idx is None
    ]

    # Should have at most num_gpus successful reservations
    assert len(successful) == num_gpus
    assert len(failed) == num_threads - num_gpus

    # All successful indices should be unique
    indices = [idx for _, idx in successful]
    assert len(set(indices)) == num_gpus


def test_gpu_assignment_snapshot_is_isolated():
    """Snapshot is a copy, not a reference to the internal map."""
    with job_processor._gpu_lock:
        job_processor._gpu_assignment_map[0] = 100

    snapshot = job_processor.get_gpu_assignment_snapshot()
    assert snapshot == {0: 100}

    # Mutating snapshot does not affect internal state
    snapshot[0] = 999
    snapshot[1] = 200

    internal = job_processor.get_gpu_assignment_snapshot()
    assert internal == {0: 100}
    assert 1 not in internal
