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
# Created by Gemini on 8/5/2025.
# Dryad and Naiad Software LLC
#
# Project: sethlans_reborn
#
# tests/e2e/test_gpu_assignment.py
"""
End-to-end tests for targeted physical GPU assignment.
"""
import queue
import uuid
import pytest
import requests
import platform
import os

from .shared_setup import BaseE2ETest, MANAGER_URL
from .helpers import poll_for_completion, is_gpu_available, is_self_hosted_runner
from sethlans_worker_agent import system_monitor
from workers.constants import RenderSettings


class TestGpuAssignment(BaseE2ETest):
    """
    Verifies that jobs can be forced to run on specific physical GPUs and that
    the assignment is correctly logged for verification.
    """

    def test_sequential_render_on_each_gpu(self):
        """
        Tests rendering a high-load job sequentially on each available physical GPU.

        This test iterates through all detected physical GPUs. For each one, it:
        1. Starts a worker specifically configured to use ONLY that GPU index.
        2. Submits a high-load render job.
        3. Waits for the job to complete successfully.
        4. Verifies from the worker's logs that the job was correctly assigned
           to the intended physical GPU.

        The test is skipped if no compatible GPUs are found on the host machine or
        if running in an unstable macOS CI environment.
        """
        is_standard_mac_ci = platform.system() == "Darwin" and "CI" in os.environ and not is_self_hosted_runner()
        if not is_gpu_available() or is_standard_mac_ci:
            pytest.skip("Skipping GPU assignment test: No compatible GPU or running in standard macOS CI.")

        system_monitor._gpu_details_cache = None  # Force a fresh detection for the test
        physical_gpus = system_monitor.get_gpu_device_details()
        num_gpus = len(physical_gpus)

        if num_gpus < 1:
            pytest.skip("Skipping GPU assignment test: No physical GPUs were detected.")

        print(f"\n--- E2E TEST: Sequential GPU Assignment for {num_gpus} GPU(s) ---")

        for gpu_index in range(num_gpus):
            worker_for_this_gpu = None
            log_thread_for_this_gpu = None
            try:
                # 1. Stop the default worker and start a dedicated one for this GPU index
                print(f"\n--- Testing GPU Index {gpu_index} ---")
                if self.worker_process and self.worker_process.poll() is None:
                    self.worker_process.kill()
                    self.worker_process.wait(timeout=10)

                self.worker_log_queue = queue.Queue()
                env = {"SETHLANS_FORCE_GPU_INDEX": str(gpu_index)}
                # The start_worker method assigns the worker and thread to the class
                self.start_worker(self.worker_log_queue, extra_env=env)
                worker_for_this_gpu = self.worker_process
                log_thread_for_this_gpu = self.worker_log_thread

                # 2. Submit a high-load render job
                job_payload = {
                    "name": f"E2E GPU Assign Test {gpu_index}-{uuid.uuid4().hex[:8]}",
                    "project": self.project_id,
                    "asset_id": self.bmw_asset_id,
                    "output_file_pattern": f"gpu_assign_test_{gpu_index}_####",
                    "start_frame": 1,
                    "end_frame": 1,
                    "blender_version": self._blender_version_for_test,
                    "render_engine": "CYCLES",
                    "render_device": "GPU",
                    "render_settings": {
                        "cycles.samples": 400,
                        "render.resolution_x": 1920,
                        "render.resolution_y": 1080
                    }
                }
                print(f"Submitting job for GPU {gpu_index}...")
                create_response = requests.post(f"{MANAGER_URL}/jobs/", json=job_payload)
                assert create_response.status_code == 201, f"Failed to create job for GPU {gpu_index}: {create_response.text}"
                job_url = f"{MANAGER_URL}/jobs/{create_response.json()['id']}/"

                # 3. Wait for completion
                print(f"Polling for job completion on GPU {gpu_index}...")
                poll_for_completion(job_url, timeout_seconds=360)

            finally:
                # 4. Clean up the worker and ensure all logs are processed
                if worker_for_this_gpu and worker_for_this_gpu.poll() is None:
                    print(f"Terminating worker for GPU {gpu_index}...")
                    worker_for_this_gpu.kill()
                    worker_for_this_gpu.wait(timeout=10)

                # --- FIX: Wait for the log thread to finish reading all output ---
                if log_thread_for_this_gpu and log_thread_for_this_gpu.is_alive():
                    log_thread_for_this_gpu.join(timeout=5)

            # 5. Verify logs for correct assignment
            print(f"Verifying logs for GPU {gpu_index} assignment...")
            worker_logs = []
            while not self.worker_log_queue.empty():
                worker_logs.append(self.worker_log_queue.get_nowait())
            full_log = "".join(worker_logs)

            print(f"\n--- CAPTURED LOGS FOR GPU {gpu_index} ---")
            print(full_log)
            print(f"--- END LOGS FOR GPU {gpu_index} ---\n")

            expected_log_line = f"Assigning to [Physical GPU {gpu_index}]"
            assert expected_log_line in full_log, \
                f"Log verification failed for GPU {gpu_index}. Expected to find '{expected_log_line}'."

            print(f"SUCCESS: Log verification passed for GPU {gpu_index}.")