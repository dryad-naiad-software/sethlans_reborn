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
# Created by Gemini on 8/4/2025.
# Dryad and Naiad Software LLC
#
# Project: sethlans_reborn
#
# tests/e2e/test_gpu_split_mode.py
"""
End-to-end tests for the multi-GPU split mode feature. 🚀
"""
import pytest
import requests
import uuid
import threading

from .shared_setup import BaseE2ETest, MANAGER_URL
from .helpers import is_gpu_available, poll_for_completion
from sethlans_worker_agent import system_monitor


class TestGPUSplitMode(BaseE2ETest):
    """
    Validates that a worker can process multiple GPU jobs in parallel when
    GPU split mode is enabled.
    """

    def test_gpu_split_mode_processes_jobs_in_parallel(self):
        """
        Tests that a worker with multiple GPUs, when started in split mode,
        can claim and complete multiple concurrent GPU jobs.
        """
        # This test requires at least 2 GPUs to be meaningful.
        system_monitor._gpu_devices_cache = None # Ensure fresh detection
        gpu_info = system_monitor.get_system_info()['available_tools'].get('gpu_devices_details', [])
        if len(gpu_info) < 2:
            pytest.skip("Skipping GPU split mode test: Requires at least 2 GPUs.")

        print("\n--- E2E TEST: Multi-GPU Split Mode Parallel Execution ---")

        # Stop the default worker and start a new one with split mode enabled.
        if self.worker_process:
            self.worker_process.kill()
        self.start_worker(self.worker_log_queue, extra_env={"SETHLANS_GPU_SPLIT_MODE": "true"})

        num_concurrent_jobs = len(gpu_info)
        job_urls = []
        threads = []

        def submit_job(url_list):
            """Target function for each thread to submit a GPU job."""
            unique_id = uuid.uuid4().hex[:8]
            job_payload = {
                "name": f"E2E GPU Split Job {unique_id}",
                "project": self.project_id,
                "asset_id": self.bmw_asset_id,
                "output_file_pattern": f"gpu_split_job_{unique_id}_####",
                "start_frame": 1, "end_frame": 1,
                "blender_version": self._blender_version_for_test,
                "render_device": "GPU"
            }
            try:
                response = requests.post(f"{MANAGER_URL}/jobs/", json=job_payload)
                response.raise_for_status()
                job_id = response.json()['id']
                job_url = f"{MANAGER_URL}/jobs/{job_id}/"
                url_list.append(job_url)
                print(f"  Successfully submitted concurrent GPU job: {job_url}")
            except requests.RequestException as e:
                print(f"  Error submitting job: {e}")

        # Submit a number of jobs equal to the number of GPUs
        print(f"Submitting {num_concurrent_jobs} GPU jobs concurrently...")
        for _ in range(num_concurrent_jobs):
            thread = threading.Thread(target=submit_job, args=(job_urls,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        assert len(job_urls) == num_concurrent_jobs, "Not all concurrent jobs were submitted successfully."

        # Poll for completion of all jobs. The test harness will fail on timeout or error.
        print("\nPolling for completion of all concurrent GPU jobs...")
        for job_url in job_urls:
            poll_for_completion(job_url, timeout_seconds=300) # Allow more time for parallel renders
            print(f"  Job {job_url} completed successfully.")

        print("\nSUCCESS: All concurrent GPU jobs completed in split mode.")