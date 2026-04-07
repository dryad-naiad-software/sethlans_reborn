# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Integration tests for the worker poll ``device_prefs`` query parameter.

FR-9 / FR-9a: the worker must advertise only the device preferences
it can currently fulfill. These tests drive
``job_processor.poll_and_claim_job`` with mocked API and assert the
``device_prefs`` CSV param assembled by ``_build_poll_params`` matches
the free-slot shape of the ``WorkerCapacity`` instance.

The marquee concurrency test (FR-29) lives in ``test_capacity_gate.py``
and shares helpers with this module via ``_capacity_harness.py``.
"""

from sethlans_worker_agent import api_handler, job_processor

from ._capacity_harness import (
    JobQueue,
    install_fake_capacity,
    stub_scan_for_local_blenders,
)


class TestPollParamsDeviceFilter:
    """FR-9a: worker sends device_prefs derived from free slots."""

    def test_split_mode_both_free_sends_all_three(self, mocker):
        """With GPU and CPU free, device_prefs includes GPU, CPU, ANY."""
        install_fake_capacity(mocker, gpu_count=1, cpu_cores=4)
        stub_scan_for_local_blenders(mocker)

        queue = JobQueue([])  # empty -- we only care about params
        poll_mock = mocker.patch.object(
            api_handler,
            'poll_for_available_jobs',
            side_effect=queue.pop,
        )
        mocker.patch.object(api_handler, 'claim_job', return_value=False)

        job_processor.poll_and_claim_job(worker_id=1)
        assert poll_mock.called
        params = poll_mock.call_args[0][0]
        assert 'device_prefs' in params
        device_prefs = set(params['device_prefs'].split(','))
        assert device_prefs == {'GPU', 'CPU', 'ANY'}

    def test_cpu_only_worker_omits_gpu_from_prefs(self, mocker):
        """A 0-GPU worker must never advertise GPU or ANY+GPU prefs."""
        install_fake_capacity(mocker, gpu_count=0, cpu_cores=4)
        stub_scan_for_local_blenders(mocker)

        queue = JobQueue([])
        poll_mock = mocker.patch.object(
            api_handler,
            'poll_for_available_jobs',
            side_effect=queue.pop,
        )
        mocker.patch.object(api_handler, 'claim_job', return_value=False)

        job_processor.poll_and_claim_job(worker_id=1)
        assert poll_mock.called
        params = poll_mock.call_args[0][0]
        device_prefs = set(params['device_prefs'].split(','))
        # No GPU slot exists, so only CPU-compatible prefs are free.
        assert 'GPU' not in device_prefs
        assert device_prefs == {'CPU', 'ANY'}

    def test_gpu_only_worker_omits_cpu_from_prefs(self, mocker):
        """A FORCE_GPU_ONLY worker advertises no CPU slot."""
        install_fake_capacity(
            mocker, gpu_count=1, cpu_cores=4, force_gpu_only=True,
        )
        stub_scan_for_local_blenders(mocker)

        queue = JobQueue([])
        poll_mock = mocker.patch.object(
            api_handler,
            'poll_for_available_jobs',
            side_effect=queue.pop,
        )
        mocker.patch.object(api_handler, 'claim_job', return_value=False)

        job_processor.poll_and_claim_job(worker_id=1)
        assert poll_mock.called
        params = poll_mock.call_args[0][0]
        device_prefs = set(params['device_prefs'].split(','))
        # No CPU slot exists on a FORCE_GPU_ONLY worker.
        assert 'CPU' not in device_prefs
        assert device_prefs == {'GPU', 'ANY'}

    def test_gpu_slot_in_use_drops_gpu_and_any(self, mocker):
        """Holding the only GPU slot limits prefs to CPU only.

        Once the worker's single GPU is reserved, ``free_device_prefs``
        must no longer advertise ``GPU`` or ``ANY`` (since an ANY job
        could dispatch to the now-full GPU path). Only ``CPU`` remains.
        """
        install_fake_capacity(mocker, gpu_count=1, cpu_cores=4)
        stub_scan_for_local_blenders(mocker)

        capacity = job_processor.get_worker_capacity()
        assert capacity is not None
        # Reserve the lone GPU slot; CPU slot remains free.
        reservation = capacity.reserve_for_job(42, 'GPU')
        assert reservation is not None

        queue = JobQueue([])
        poll_mock = mocker.patch.object(
            api_handler,
            'poll_for_available_jobs',
            side_effect=queue.pop,
        )
        mocker.patch.object(api_handler, 'claim_job', return_value=False)

        job_processor.poll_and_claim_job(worker_id=1)
        assert poll_mock.called
        params = poll_mock.call_args[0][0]
        device_prefs = set(params['device_prefs'].split(','))
        # GPU is held; only CPU and ANY (served by CPU) remain free.
        assert 'GPU' not in device_prefs
        assert device_prefs == {'CPU', 'ANY'}
