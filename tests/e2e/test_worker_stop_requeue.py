# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
E2E: a Blender job survives its worker stopping mid-render (#246, #247).

* Graceful stop: the worker is asked to shut down through its web UI
  control endpoint while Blender renders. It stops Blender, leaves no
  partial frame, hands the job back (QUEUED, "Interrupted:
  worker_shutdown", yield_requeue_count + 1, auto_requeue_count
  unchanged) and exits. Started again, it renders the job to DONE.
* Hard kill: the worker agent process is killed while Blender renders
  and a new process with the same identity starts within the
  staleness window. Its registration requeues the job ("Auto-requeued:
  worker restarted during Blender render", auto_requeue_count + 1) and
  it renders the job to DONE.

In both tests every status the job passes through is recorded and must
never be CANCELED or ERROR. The global queue is paused around the hard
kill restart only so the requeued job can be observed in QUEUED before
the new process claims it again. ``yield_requeue_count`` and
``auto_requeue_count`` are not in the job API; they are read from the
manager's test database (read-only).
"""

import logging
import time

import psutil
import pytest

from tests.e2e import worker_stop_procs as procs
from tests.e2e.helpers import verify_image_output
from tests.e2e.worker_stop_harness import JobWatcher, WorkerStopHarness

logger = logging.getLogger(__name__)

# The first render of the class downloads Blender (cold worker).
FIRST_RENDER_TIMEOUT = 1500
RENDER_TIMEOUT = 600
EXIT_TIMEOUT = 120
STALENESS_SECONDS = 90
RESTART_MESSAGE = "Auto-requeued: worker restarted during Blender render"
FINISHED = ("DONE", "ERROR", "CANCELED")


def _assert_done_with_output(harness, watcher, worker_id):
    job = watcher.wait_for(lambda j: j["status"] in FINISHED, RENDER_TIMEOUT, "the job to finish")
    assert job["status"] == "DONE", f"Job did not render: {job}"
    logger.info("Job %s DONE, render_time_seconds=%s", job["id"], job.get("render_time_seconds"))
    assert job["output_file"], f"DONE without an output file: {job}"
    assert job["assigned_worker"] == worker_id, "Rendered by a worker with another identity"
    verify_image_output(harness.session, job["output_file"], harness.base_url)
    return job


def _assert_never_failed(watcher):
    seen = watcher.statuses()
    logger.info("Job status sequence: %s", seen)
    assert not {"CANCELED", "ERROR"} & set(seen), f"Job passed through a failure state: {watcher.history}"


# Two full CPU renders, a Blender download and two worker starts: well over
# the per-test limit some CI runs apply to the end-to-end suite.
@pytest.mark.timeout(1800)
class TestWorkerStopRequeue(WorkerStopHarness):
    """Worker stop and restart during a Blender render."""

    def _render_until_interruptible(self, name, timeout):
        self.ensure_worker_running()
        job_id = self.submit_job(name)
        watcher = JobWatcher(self.session, self.base_url, job_id)
        renders = self.wait_for_render(watcher, f"{name}_####", timeout)
        worker_id = watcher.last["assigned_worker"]
        assert worker_id is not None
        logger.info("Rendering job %s: Blender %s, parents %s", job_id, procs.describe(renders),
                    [p.ppid() for p in renders])
        return job_id, watcher, renders, worker_id

    def test_graceful_stop_hands_job_back_and_restart_renders_it(self):
        name = "graceful_stop"
        job_id, watcher, renders, worker_id = self._render_until_interruptible(name, FIRST_RENDER_TIMEOUT)
        old_proc, old_pid = self.worker_proc, self.worker_pid
        assert self.requeue_counts(job_id) == (0, 0)

        answer = self.request_graceful_stop()
        assert answer == {"status": "shutting_down"}
        stop_requested = time.monotonic()
        deadline = stop_requested + EXIT_TIMEOUT
        while old_proc.poll() is None and time.monotonic() < deadline:
            watcher.poll()
            time.sleep(0.5)
        assert old_proc.poll() is not None, f"Worker still running {EXIT_TIMEOUT}s after the shutdown request"
        logger.info("Worker exited rc=%s %.1fs after the shutdown request",
                    old_proc.returncode, time.monotonic() - stop_requested)
        assert old_proc.returncode == 0
        assert not procs.is_alive(old_pid), "Worker agent outlived its launcher"

        survivors = procs.blender_renders(f"{name}_####")
        assert not survivors, f"Blender survived the graceful stop: {procs.describe(survivors)}"
        assert all(not p.is_running() for p in renders)

        job = watcher.poll()
        assert job["status"] == "QUEUED", job
        assert job["error_message"] == "Interrupted: worker_shutdown", job
        assert job["assigned_worker"] is None, job
        assert self.requeue_counts(job_id) == (0, 1)  # (auto, yield)
        partial = sorted(self.output_dir.glob(f"renders/{name}_*"))
        assert not partial, f"Partial frame left in the worker output folder: {partial}"

        self.start_worker_process()
        _assert_done_with_output(self, watcher, worker_id)
        assert self.requeue_counts(job_id) == (0, 1)
        _assert_never_failed(watcher)
        assert watcher.statuses()[-1] == "DONE"
        assert watcher.statuses().count("RENDERING") >= 2, watcher.history

    def test_hard_kill_and_quick_restart_requeues_and_renders(self):
        name = "hard_kill"
        job_id, watcher, renders, worker_id = self._render_until_interruptible(name, RENDER_TIMEOUT)
        old_proc, old_pid = self.worker_proc, self.worker_pid
        self.set_queue_paused(True)  # hold the requeued job in QUEUED to observe it
        try:
            psutil.Process(old_pid).kill()
            killed_at = time.monotonic()
            old_proc.wait(timeout=30)
            # Give the OS a moment to tear down anything tied to the agent.
            psutil.wait_procs(renders, timeout=3)
            self._observe_and_kill_orphans(name)

            job = watcher.poll()
            assert job["status"] == "RENDERING", f"Requeued before any restart: {job}"
            self.start_worker_process()
            restart_delay = time.monotonic() - killed_at
            logger.info("New worker process started %.1fs after the kill", restart_delay)
            assert restart_delay < STALENESS_SECONDS

            job = watcher.wait_for(lambda j: j["status"] != "RENDERING", STALENESS_SECONDS,
                                   "the restart to requeue the job")
            requeued_after = time.monotonic() - killed_at
            logger.info("Job left RENDERING %.1fs after the kill: %s %r",
                        requeued_after, job["status"], job["error_message"])
            assert job["status"] == "QUEUED", job
            assert job["error_message"] == RESTART_MESSAGE, job
            assert job["assigned_worker"] is None, job
            assert requeued_after < STALENESS_SECONDS, "Requeued by the staleness reaper, not the restart"
            assert self.requeue_counts(job_id) == (1, 0)  # (auto, yield)
        finally:
            self.set_queue_paused(False)

        _assert_done_with_output(self, watcher, worker_id)
        assert self.requeue_counts(job_id) == (1, 0)
        _assert_never_failed(watcher)
        assert watcher.statuses()[-1] == "DONE"

    def _observe_and_kill_orphans(self, name):
        """Record what outlived the killed agent (#250 on Windows), then kill it.

        Observation only: a Blender that keeps running after its worker
        was hard-killed is a known Windows behaviour tracked separately.
        The leftovers are this test's own processes; they are killed
        before the restart so the new worker renders alone.
        """
        blenders = procs.blender_renders(f"{name}_####")
        others = [p for p in procs.procs_matching(self.worker_scope)
                  if p.pid not in {b.pid for b in blenders}]
        logger.warning(
            "OBSERVATION after hard kill of the worker agent: Blender render processes still "
            "running: %s; other worker child processes still running: %s",
            procs.describe(blenders) or "none", procs.describe(others) or "none",
        )
        alive = procs.kill_all(blenders + others)
        assert not alive, f"Could not clean up after the kill: {procs.describe(alive)}"
