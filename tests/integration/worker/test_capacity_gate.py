# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
FR-29 integration test for the worker capacity gate.

Drives ``job_processor.get_and_claim_job`` repeatedly against a mocked
API surface with a deterministic capacity profile, while a sampling
thread snapshots ``get_active_jobs_snapshot()`` at fixed intervals. The
invariant under test is:

    max(observed_active_job_counts) <= total_capacity

``blender_executor.execute_blender_job`` is replaced with a stub that
sleeps for a non-trivial duration so that real concurrency is exercised
(without the sleep, jobs would complete instantly and the concurrency
invariant would never be tested).

The related FR-9 ``free_device_prefs`` -> ``device_prefs`` poll param
tests live in ``test_poll_params_device_filter.py`` alongside the
shared harness in ``_capacity_harness.py``. Keeping that split holds
each file below the 300-line project ceiling.
"""

import threading
import time

from sethlans_worker_agent import (
    api_handler,
    blender_executor,
    job_processor,
)

from ._capacity_harness import (
    JobQueue,
    install_fake_capacity,
    make_queued_jobs,
    sleeping_blender_stub,
    stub_scan_for_local_blenders,
)


class TestCapacityGateEndToEnd:
    """FR-29: worker never dispatches more than ``total_capacity`` jobs."""

    def test_never_exceeds_capacity_under_oversupply(self, mocker):
        """Seed total_capacity + 5 jobs, assert concurrency <= total_capacity.

        Runs a driver thread that repeatedly calls
        ``job_processor.get_and_claim_job`` (mirroring one iteration of
        the agent main loop's capacity-gated claim path) and a sampling
        thread that snapshots ``get_active_jobs_snapshot()`` every
        ~50ms. The invariant is that no snapshot ever shows more active
        jobs than the profile reports as ``total``.
        """
        profile = install_fake_capacity(
            mocker, gpu_count=1, cpu_cores=4,
        )
        total_capacity = profile.total
        # 1 GPU + 1 CPU on a 4-core box should produce 2 slots.
        assert total_capacity == 2

        stub_scan_for_local_blenders(mocker)

        job_count = total_capacity + 5
        queue = JobQueue(make_queued_jobs(job_count))

        mocker.patch.object(
            api_handler,
            'poll_for_available_jobs',
            side_effect=queue.pop,
        )
        mocker.patch.object(
            api_handler, 'claim_job', return_value=True,
        )
        mocker.patch.object(
            api_handler, 'update_job_status', return_value=True,
        )
        mocker.patch.object(
            api_handler, 'upload_render_output', return_value=True,
        )
        mocker.patch.object(
            blender_executor,
            'execute_blender_job',
            side_effect=sleeping_blender_stub(0.5),
        )

        stop_event = threading.Event()
        observed_lengths = []
        dispatched_threads = []

        def driver():
            """Mirror the agent main loop's capacity-gated claim path."""
            while not stop_event.is_set():
                if job_processor.capacity_is_full():
                    time.sleep(0.02)
                    continue
                thread = job_processor.get_and_claim_job(worker_id=1)
                if thread is not None:
                    dispatched_threads.append(thread)
                time.sleep(0.02)

        def sampler():
            """Poll the active-jobs snapshot on a tight interval."""
            while not stop_event.is_set():
                snapshot = job_processor.get_active_jobs_snapshot()
                observed_lengths.append(len(snapshot))
                assert len(snapshot) <= total_capacity, (
                    f"Active job count exceeded total_capacity: "
                    f"{len(snapshot)} > {total_capacity}"
                )
                time.sleep(0.05)

        driver_thread = threading.Thread(target=driver, name='driver')
        sampler_thread = threading.Thread(target=sampler, name='sampler')

        try:
            sampler_thread.start()
            driver_thread.start()
            # Observation window. With 2 slots, a 0.5s blender stub and
            # job_count=7, all jobs should drain in ~2s; we watch for
            # 4s to catch any late over-claim.
            time.sleep(4.0)
        finally:
            stop_event.set()
            driver_thread.join(timeout=5)
            sampler_thread.join(timeout=5)
            for t in dispatched_threads:
                t.join(timeout=5)

        assert not driver_thread.is_alive()
        assert not sampler_thread.is_alive()
        assert observed_lengths, "Sampler produced no observations."
        # The invariant the test exists to verify.
        assert max(observed_lengths) <= total_capacity
        # All jobs must have drained through the capacity-gated path.
        assert queue.remaining() == 0
        # Every dispatched thread should have completed; _active_jobs
        # should be empty once every slot released.
        assert job_processor.get_active_jobs_snapshot() == {}

    def test_no_new_claims_while_at_capacity(self, mocker):
        """capacity_is_full() closing the gate blocks further claims.

        Reserves every slot on a ``WorkerCapacity`` directly, then
        verifies that ``poll_and_claim_job`` is never invoked by the
        gate-respecting caller and would touch no API had it been.
        """
        profile = install_fake_capacity(
            mocker, gpu_count=1, cpu_cores=4,
        )
        capacity = job_processor.get_worker_capacity()
        assert capacity is not None
        assert profile.total == 2

        # Fill every slot.
        gpu_res = capacity.reserve_for_job(10, 'GPU')
        cpu_res = capacity.reserve_for_job(11, 'CPU')
        assert gpu_res is not None and cpu_res is not None
        assert job_processor.capacity_is_full() is True

        poll_mock = mocker.patch.object(
            api_handler, 'poll_for_available_jobs',
        )
        claim_mock = mocker.patch.object(api_handler, 'claim_job')

        # Simulate the agent main loop's gate check.
        result = None
        if not job_processor.capacity_is_full():
            result = job_processor.poll_and_claim_job(worker_id=1)

        assert result is None
        # Gate was closed -- poll_and_claim_job should not have run at
        # all, so the API is never touched.
        poll_mock.assert_not_called()
        claim_mock.assert_not_called()

        # Releasing a slot reopens the gate.
        capacity.release_slot(10)
        assert job_processor.capacity_is_full() is False
