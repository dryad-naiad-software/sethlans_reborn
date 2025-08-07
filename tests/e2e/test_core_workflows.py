#
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
#
#
# Created By Mario Estrella on 8/1/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
# tests/e2e/test_core_workflows.py
"""
End-to-end tests for the most fundamental rendering workflows, including
single-frame CPU and GPU jobs.
"""

import platform
import os
import queue
import pytest
import requests
import uuid
import time

from .shared_setup import BaseE2ETest, MANAGER_URL
from .helpers import is_gpu_available, poll_for_completion, verify_image_output
from workers.constants import RenderSettings


class TestCoreWorkflows(BaseE2ETest):
    """
    Validates the entire process for a single-frame CPU render job, from
    API submission to final file verification.
    """

    def test_single_frame_cpu_render(self):
        """
        Tests the complete lifecycle of a single-frame CPU render job.

        This test now includes an explicit check to ensure that when a worker
        claims a job, the `assigned_worker` and `started_at` fields are
        correctly updated via the API. This serves as a regression test for
        the serializer `read_only_fields` bug.
        """
        print("\n--- E2E TEST: Single-Frame CPU Render ---")
        job_payload = {
            "name": f"E2E CPU Render Test {uuid.uuid4().hex[:8]}",
            "project": self.project_id,
            "asset_id": self.scene_asset_id,
            "output_file_pattern": "e2e_cpu_render_####",
            "start_frame": 1,
            "end_frame": 1,
            "blender_version": self._blender_version_for_test,
            "render_engine": "CYCLES",
            "render_device": "CPU",
            "render_settings": {
                RenderSettings.SAMPLES: 10,
                RenderSettings.RESOLUTION_X: 640,
                RenderSettings.RESOLUTION_Y: 480
            }
        }
        create_response = requests.post(f"{MANAGER_URL}/jobs/", json=job_payload)
        assert create_response.status_code == 201
        job_id = create_response.json()['id']
        job_url = f"{MANAGER_URL}/jobs/{job_id}/"

        # --- NEW: Regression test for serializer bug ---
        print("Waiting for worker to claim the job and set timestamps...")
        start_time = time.time()
        job_claimed = False
        while time.time() - start_time < 30:  # 30 second timeout to claim
            response = requests.get(job_url)
            response.raise_for_status()
            data = response.json()
            if data['status'] == 'RENDERING':
                print("Job is RENDERING. Verifying claim data...")
                assert data['assigned_worker'] is not None, "Job is rendering but 'assigned_worker' is null."
                assert data['started_at'] is not None, "Job is rendering but 'started_at' is null."
                job_claimed = True
                break
            time.sleep(1)

        assert job_claimed, "Worker failed to claim the job and update its status to RENDERING within the time limit."
        print("SUCCESS: Worker correctly claimed job and updated timestamps.")
        # --- End of regression test ---

        print(f"Job submitted. Polling for completion at {job_url}...")
        final_job_data = poll_for_completion(job_url)

        print("Verifying final job data and outputs...")
        assert final_job_data['render_time_seconds'] > 0
        verify_image_output(final_job_data['output_file'])
        verify_image_output(final_job_data['thumbnail'])
        print("SUCCESS: Single-frame CPU render workflow completed and verified.")

    def test_single_frame_gpu_render(self):
        """
        Tests the complete lifecycle of a single-frame GPU render job.
        This test is skipped at runtime if no compatible GPU is detected.
        """
        # Perform the check at runtime, after setup_class has prepared Blender.
        if not is_gpu_available() or (platform.system() == "Darwin" and "CI" in os.environ):
            pytest.skip("Skipping GPU test: No compatible GPU or running in unstable macOS CI.")

        print("\n--- E2E TEST: Single-Frame GPU Render ---")
        job_payload = {
            "name": f"E2E GPU Render Test {uuid.uuid4().hex[:8]}",
            "project": self.project_id,
            "asset_id": self.bmw_asset_id,  # Use a more intensive scene for GPU
            "output_file_pattern": "e2e_gpu_render_####",
            "start_frame": 1,
            "end_frame": 1,
            "blender_version": self._blender_version_for_test,
            "render_engine": "CYCLES",
            "render_device": "GPU",
            "render_settings": {
                # bmw27.blend is 1920x1080, 35% is 672x378, which is sufficient
                RenderSettings.RESOLUTION_PERCENTAGE: 35
            }
        }
        create_response = requests.post(f"{MANAGER_URL}/jobs/", json=job_payload)
        assert create_response.status_code == 201
        job_id = create_response.json()['id']
        job_url = f"{MANAGER_URL}/jobs/{job_id}/"

        print(f"Job submitted. Polling for completion at {job_url}...")
        final_job_data = poll_for_completion(job_url, timeout_seconds=240)  # Allow more time for GPU scene

        print("Verifying final job data and outputs...")
        assert final_job_data['render_time_seconds'] > 0
        verify_image_output(final_job_data['output_file'])
        verify_image_output(final_job_data['thumbnail'])
        print("SUCCESS: Single-frame GPU render workflow completed and verified.")

    def test_cpu_render_with_thread_limit(self):
        """
        Tests that a worker with a configured CPU thread limit correctly
        applies the --threads flag to the Blender command.

        The worker is configured by setting the SETHLANS_WORKER_CPU_THREADS
        environment variable, which is the name constructed by the config
        system from the 'worker' section and 'cpu_threads' key.
        """
        print("\n--- E2E TEST: CPU Render with Thread Limit ---")
        # 1. Stop default worker and start one with the thread limit
        if self.worker_process and self.worker_process.poll() is None:
            self.worker_process.kill()
            self.worker_process.wait(timeout=10)

        # Use a new queue to isolate logs for this specific worker run
        log_queue = queue.Queue()
        # --- FIX: Corrected environment variable name ---
        self.start_worker(log_queue, extra_env={"SETHLANS_WORKER_CPU_THREADS": "2"})

        # 2. Submit a CPU job
        job_payload = {
            "name": f"E2E CPU Thread Limit Test {uuid.uuid4().hex[:8]}",
            "project": self.project_id,
            "asset_id": self.scene_asset_id,
            "output_file_pattern": "e2e_cpu_threads_####",
            "start_frame": 1, "end_frame": 1,
            "blender_version": self._blender_version_for_test,
            "render_device": "CPU",
        }
        create_response = requests.post(f"{MANAGER_URL}/jobs/", json=job_payload)
        assert create_response.status_code == 201
        job_url = f"{MANAGER_URL}/jobs/{create_response.json()['id']}/"

        # 3. Wait for completion
        print(f"Job submitted. Polling for completion at {job_url}...")
        poll_for_completion(job_url)

        # 4. Stop worker and inspect logs
        if self.worker_process and self.worker_process.poll() is None:
            self.worker_process.kill()
            self.worker_process.wait(timeout=10)
        if self.worker_log_thread and self.worker_log_thread.is_alive():
            self.worker_log_thread.join(timeout=5)

        worker_logs = []
        while not log_queue.empty():
            worker_logs.append(log_queue.get_nowait())
        full_log = "".join(worker_logs)

        # 5. Verify the command line in the log
        assert "Running Command:" in full_log, "Could not find command log line."
        assert "--threads 2" in full_log, "The '--threads 2' flag was not found in the worker's command log."

        print("SUCCESS: Worker correctly applied the CPU thread limit.")