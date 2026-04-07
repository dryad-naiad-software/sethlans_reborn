# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Shared test helpers for worker capacity gate integration tests.

Module name starts with an underscore so pytest does not collect it
as a test module. Keeps ``test_capacity_gate.py`` and
``test_poll_params_device_filter.py`` under the 300-line file limit
by centralising the capacity-installation and blender-stub plumbing
both files need.
"""

import threading
import time

from sethlans_worker_agent import config, job_processor
from sethlans_worker_agent.capacity import (
    CapacityProfile,
    compute_capacity_profile,
)


def install_fake_capacity(
    mocker,
    *,
    gpu_count: int,
    cpu_cores: int,
    gpu_mode: str = 'split',
    force_cpu_only: bool = False,
    force_gpu_only: bool = False,
    force_gpu_index=None,
    cpu_threads: int = 0,
) -> CapacityProfile:
    """Patch capacity-detection hooks and call ``init_capacity``.

    Returns the resolved ``CapacityProfile`` so tests can assert the
    slot counts they seeded.
    """
    gpu_list = [
        {'name': f'GPU-{i}', 'type': 'CUDA', 'index': i}
        for i in range(gpu_count)
    ]
    mocker.patch(
        'sethlans_worker_agent.job_processor.system_monitor'
        '.get_gpu_device_details',
        return_value=gpu_list,
    )
    mocker.patch(
        'sethlans_worker_agent.job_processor.system_monitor'
        '.get_cpu_thread_count',
        return_value=cpu_cores,
    )
    mocker.patch.object(config, 'FORCE_CPU_ONLY', force_cpu_only)
    mocker.patch.object(config, 'FORCE_GPU_ONLY', force_gpu_only)
    mocker.patch.object(config, 'FORCE_GPU_INDEX', force_gpu_index)
    mocker.patch.object(config, 'GPU_MODE', gpu_mode)
    mocker.patch.object(config, 'CPU_THREADS', cpu_threads)

    capacity = job_processor.init_capacity()

    # Sanity-check that our forced hardware survived the real
    # compute_capacity_profile call. If the formula ever changes, this
    # assertion will point directly at the drift.
    expected = compute_capacity_profile(
        detected_gpu_count=gpu_count,
        cpu_cores=cpu_cores,
        force_cpu_only=force_cpu_only,
        force_gpu_only=force_gpu_only,
        force_gpu_index=force_gpu_index,
        gpu_mode=gpu_mode,
        cpu_threads_config=cpu_threads,
    )
    assert capacity.profile == expected
    return capacity.profile


def stub_scan_for_local_blenders(mocker):
    """Force scan_for_local_blenders into a deterministic empty list.

    job_processor._build_poll_params calls this to populate the
    available_versions param. A non-deterministic result would create
    flaky poll-params snapshots in tests that assert on them.
    """
    mocker.patch(
        'sethlans_worker_agent.job_processor.tool_manager_instance'
        '.scan_for_local_blenders',
        return_value=[],
    )


def sleeping_blender_stub(sleep_seconds: float):
    """Return a fake ``execute_blender_job`` that sleeps then succeeds.

    Mimics a non-trivial Blender render so concurrent claims actually
    overlap in time, which is what the capacity invariant is testing.
    """

    def _stub(job_data, assigned_gpu_index=None):
        time.sleep(sleep_seconds)
        return (True, False, '', '', '', None, None)

    return _stub


class JobQueue:
    """Thread-safe pop-front job queue for the mocked poll endpoint.

    Each call to ``poll_for_available_jobs`` returns a list containing
    at most one job dict, mirroring ``job_processor.poll_and_claim_job``
    which only claims ``available_jobs[0]``.
    """

    def __init__(self, jobs):
        self._jobs = list(jobs)
        self._lock = threading.Lock()

    def pop(self, _params=None):
        with self._lock:
            if not self._jobs:
                return []
            return [self._jobs.pop(0)]

    def remaining(self):
        with self._lock:
            return len(self._jobs)


def make_queued_jobs(count: int, device_pref: str = 'ANY'):
    """Produce ``count`` minimal job dicts matching the poll payload."""
    return [
        {
            'id': i,
            'name': f'Job-{i}',
            'render_device': device_pref,
            'render_engine': 'CYCLES',
            'status': 'QUEUED',
        }
        for i in range(1, count + 1)
    ]
