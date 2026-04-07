# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Integration tests for GPU slot concurrency under ``WorkerCapacity``.

Originally authored against the legacy ``job_processor._gpu_lock`` and
``_gpu_assignment_map`` primitives. Those were deleted in the worker
capacity gate feature (issue #48); the coverage they provided is
preserved here against the new ``WorkerCapacity.reserve_for_job`` and
``release_slot`` surface.

Uses real threads with short timeouts to verify thread safety of slot
reservation and release under concurrent access. Covers:

* one-thread-per-GPU -- each physical GPU is claimed by at most one job
  at a time (split mode);
* release-then-reacquire -- after ``release_slot`` the slot is available
  to another job;
* all-GPUs-busy -- ``reserve_for_job`` returns ``None`` when every GPU
  slot is held;
* no-GPUs -- ``reserve_for_job(device_pref='GPU')`` returns ``None`` on
  a worker with zero GPU slots;
* concurrent reservations -- N contending threads never receive
  duplicate GPU indices;
* GPU assignment snapshot isolation -- ``gpu_assignments_snapshot``
  returns a defensive copy.
"""

import threading

from sethlans_worker_agent.capacity import (
    WorkerCapacity,
    compute_capacity_profile,
)


def _build_capacity(num_gpus: int, cores: int = 16) -> WorkerCapacity:
    """Construct a WorkerCapacity with the requested number of GPU slots.

    Uses split mode so that ``gpu_slot_count == num_gpus``.
    """
    profile = compute_capacity_profile(
        detected_gpu_count=num_gpus,
        cpu_cores=cores,
        force_cpu_only=False,
        force_gpu_only=False,
        force_gpu_index=None,
        gpu_mode='split',
        cpu_threads_config=0,
    )
    assert profile.gpu_slot_count == num_gpus
    return WorkerCapacity(profile)


def _reserved_gpu_index(reservation):
    """Extract the single GPU index from a split-mode reservation."""
    assert reservation is not None
    assert reservation.device_used == 'GPU'
    assert len(reservation.gpu_indices) == 1
    return reservation.gpu_indices[0]


# -- Multiple threads reserving GPUs --

def test_only_one_thread_per_gpu():
    """Each GPU can only be reserved by one job at a time."""
    capacity = _build_capacity(num_gpus=2)

    results = {}
    barrier = threading.Barrier(2, timeout=5)

    def reserve(job_id):
        barrier.wait()
        reservation = capacity.reserve_for_job(job_id, 'GPU')
        results[job_id] = reservation

    t1 = threading.Thread(target=reserve, args=(100,))
    t2 = threading.Thread(target=reserve, args=(200,))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    # Both should succeed but with different GPU indices
    idx1 = _reserved_gpu_index(results[100])
    idx2 = _reserved_gpu_index(results[200])
    assert idx1 != idx2
    assert {idx1, idx2} == {0, 1}


def test_gpu_release_allows_next_thread():
    """After releasing a GPU, another job can acquire it."""
    capacity = _build_capacity(num_gpus=1)

    # First job reserves the only GPU
    first = capacity.reserve_for_job(100, 'GPU')
    assert _reserved_gpu_index(first) == 0

    # Second job cannot acquire
    second = capacity.reserve_for_job(200, 'GPU')
    assert second is None

    # Release GPU from first job
    capacity.release_slot(100)

    # Now third job can acquire the same index
    third = capacity.reserve_for_job(300, 'GPU')
    assert _reserved_gpu_index(third) == 0


def test_all_gpus_busy_returns_none():
    """When all GPUs are busy, reservation returns None."""
    capacity = _build_capacity(num_gpus=2)

    first = capacity.reserve_for_job(100, 'GPU')
    second = capacity.reserve_for_job(200, 'GPU')
    assert first is not None
    assert second is not None

    # All GPUs taken
    third = capacity.reserve_for_job(300, 'GPU')
    assert third is None


def test_no_gpus_returns_none():
    """When no GPUs exist, reservation for a GPU pref returns None."""
    capacity = _build_capacity(num_gpus=0)

    # GPU explicitly requested -- no GPU slot exists.
    assert capacity.reserve_for_job(100, 'GPU') is None


# -- Thread safety under concurrent access --

def test_concurrent_gpu_reservations_no_duplicates():
    """Many threads competing for GPUs never get duplicate assignments."""
    num_gpus = 4
    num_threads = 20
    capacity = _build_capacity(num_gpus=num_gpus)

    results = []
    results_lock = threading.Lock()
    barrier = threading.Barrier(num_threads, timeout=10)

    def reserve(job_id):
        barrier.wait()
        reservation = capacity.reserve_for_job(job_id, 'GPU')
        with results_lock:
            results.append((job_id, reservation))

    threads = [
        threading.Thread(target=reserve, args=(i,))
        for i in range(num_threads)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    successful = [
        (job_id, res) for job_id, res in results if res is not None
    ]
    failed = [
        (job_id, res) for job_id, res in results if res is None
    ]

    # Should have exactly num_gpus successful reservations
    assert len(successful) == num_gpus
    assert len(failed) == num_threads - num_gpus

    # All successful indices should be unique
    indices = [res.gpu_indices[0] for _, res in successful]
    assert len(set(indices)) == num_gpus
    assert set(indices) == set(range(num_gpus))


def test_concurrent_reservation_and_release_stays_consistent():
    """Churning reserve/release under contention leaves no leaked slots.

    Runs ``num_threads`` workers that each repeatedly reserve a GPU and
    release it. At the end, every slot must be free (``is_full() ==
    False`` and all GPUs reclaimable).
    """
    num_gpus = 2
    num_threads = 8
    iterations = 25
    capacity = _build_capacity(num_gpus=num_gpus)

    errors = []

    def churn(job_id_base):
        try:
            for i in range(iterations):
                job_id = job_id_base * 1000 + i
                reservation = capacity.reserve_for_job(job_id, 'GPU')
                if reservation is not None:
                    capacity.release_slot(job_id)
        except BaseException as exc:  # noqa: BLE001 - surface in main thread
            errors.append(exc)

    threads = [
        threading.Thread(target=churn, args=(tid,))
        for tid in range(1, num_threads + 1)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
        assert not t.is_alive(), "Churn thread did not finish in time."

    assert not errors, f"Errors in churn threads: {errors}"
    # After churn, all slots must be free and reclaimable.
    assert capacity.gpu_assignments_snapshot() == {}
    assert capacity.is_full() is False
    # Fresh reservation after the storm must still succeed.
    fresh = capacity.reserve_for_job(9001, 'GPU')
    assert _reserved_gpu_index(fresh) == 0


def test_gpu_assignment_snapshot_is_isolated():
    """Snapshot is a defensive copy, not a reference to the internal map."""
    capacity = _build_capacity(num_gpus=2)
    reservation = capacity.reserve_for_job(100, 'GPU')
    idx = _reserved_gpu_index(reservation)

    snapshot = capacity.gpu_assignments_snapshot()
    assert snapshot == {idx: 100}

    # Mutating the snapshot does not affect internal state
    snapshot[idx] = 999
    snapshot[999] = 777

    internal = capacity.gpu_assignments_snapshot()
    assert internal == {idx: 100}
    assert 999 not in internal


def test_release_slot_is_idempotent():
    """Calling release_slot on a job_id with no reservation is a no-op.

    FR-13 contract: release_slot must never raise and must silently
    become a DEBUG-only no-op when called for an unknown job_id or a
    job_id whose slot has already been released.
    """
    capacity = _build_capacity(num_gpus=1)

    # No reservation yet -- release is a no-op.
    capacity.release_slot(100)
    assert capacity.gpu_assignments_snapshot() == {}

    # Reserve, release, release again -- second release is a no-op.
    reservation = capacity.reserve_for_job(100, 'GPU')
    assert reservation is not None
    capacity.release_slot(100)
    capacity.release_slot(100)
    assert capacity.gpu_assignments_snapshot() == {}
    assert capacity.is_full() is False
