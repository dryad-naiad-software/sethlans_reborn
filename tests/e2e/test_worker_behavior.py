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
# Created by Mario Estrella on 8/1/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
# tests/e2e/test_worker_behavior.py
"""
End-to-end tests for core worker agent behavior and logic.
"""

import os
import platform
import pytest
import requests
from .shared_setup import BaseE2ETest, MANAGER_URL
from .helpers import is_gpu_available, poll_for_completion
from sethlans_worker_agent import system_monitor


class TestWorkerBehavior(BaseE2ETest):
    """
    Validates worker registration, hardware reporting, and job handling flexibility.
    """

    def test_worker_reports_correct_gpu_devices(self):
        """
        Verifies that a worker correctly detects and reports its available GPUs.
        """
        if not is_gpu_available():
            pytest.skip("Requires a GPU-capable host for a meaningful comparison.")

        print("\n--- E2E TEST: Worker GPU Hardware Reporting ---")

        # Get the ground truth by running the detection logic directly in the test
        print("Detecting local GPU devices for comparison...")
        local_devices = system_monitor.detect_gpu_devices()
        print(f"Locally detected devices: {local_devices}")

        print("Querying API for worker's reported data...")
        response = requests.get(f"{MANAGER_URL}/heartbeat/")
        assert response.status_code == 200
        # Assuming only one worker is running in the test environment
        assert len(response.json()) > 0, "No worker has registered with the manager."
        worker_data = response.json()[0]

        reported_devices = worker_data.get('available_tools', {}).get('gpu_devices', [])
        print(f"Worker reported devices via API: {reported_devices}")

        assert set(local_devices) == set(reported_devices), "Worker's reported GPUs do not match locally detected GPUs."
        print("SUCCESS: Worker correctly reported its GPU capabilities.")

    def test_gpu_capable_worker_handles_cpu_and_gpu_jobs(self):
        """
        Verifies that a default (non-forced) GPU-capable worker can process
        both CPU-only and GPU-only jobs.
        """
        if not is_gpu_available() or (platform.system() == "Darwin" and "CI" in os.environ):
            pytest.skip("Requires a stable GPU-capable host.")

        print("\n--- E2E TEST: Default Worker Job Flexibility ---")

        # Submit a GPU-only job
        gpu_payload = {
            "name": f"E2E Worker-Flex GPU Job {self.project_id}",
            "project": self.project_id,
            "asset_id": self.scene_asset_id,
            "output_file_pattern": "flex_gpu_####",
            "start_frame": 1,
            "end_frame": 1,
            "blender_version": self._blender_version_for_test,
            "render_device": "GPU"
        }
        gpu_res = requests.post(f"{MANAGER_URL}/jobs/", json=gpu_payload)
        assert gpu_res.status_code == 201
        gpu_job_url = f"{MANAGER_URL}/jobs/{gpu_res.json()['id']}/"
        print(f"Submitted GPU-only job: {gpu_job_url}")

        # Submit a CPU-only job
        cpu_payload = {
            "name": f"E2E Worker-Flex CPU Job {self.project_id}",
            "project": self.project_id,
            "asset_id": self.scene_asset_id,
            "output_file_pattern": "flex_cpu_####",
            "start_frame": 1,
            "end_frame": 1,
            "blender_version": self._blender_version_for_test,
            "render_device": "CPU"
        }
        cpu_res = requests.post(f"{MANAGER_URL}/jobs/", json=cpu_payload)
        assert cpu_res.status_code == 201
        cpu_job_url = f"{MANAGER_URL}/jobs/{cpu_res.json()['id']}/"
        print(f"Submitted CPU-only job: {cpu_job_url}")

        print("Waiting for both jobs to complete...")
        final_gpu_data = poll_for_completion(gpu_job_url, timeout_seconds=180)
        final_cpu_data = poll_for_completion(cpu_job_url, timeout_seconds=180)

        assert final_gpu_data['status'] == 'DONE'
        assert final_cpu_data['status'] == 'DONE'
        print("SUCCESS: Default GPU-capable worker successfully completed both CPU and GPU jobs.")