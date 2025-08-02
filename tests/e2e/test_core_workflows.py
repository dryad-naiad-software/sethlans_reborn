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
import pytest
import requests

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
        """
        print("\n--- E2E TEST: Single-Frame CPU Render ---")
        job_payload = {
            "name": f"E2E CPU Render Test {self.project_id}",
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
                RenderSettings.RESOLUTION_PERCENTAGE: 10
            }
        }
        create_response = requests.post(f"{MANAGER_URL}/jobs/", json=job_payload)
        assert create_response.status_code == 201
        job_id = create_response.json()['id']
        job_url = f"{MANAGER_URL}/jobs/{job_id}/"

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
            "name": f"E2E GPU Render Test {self.project_id}",
            "project": self.project_id,
            "asset_id": self.bmw_asset_id,  # Use a more intensive scene for GPU
            "output_file_pattern": "e2e_gpu_render_####",
            "start_frame": 1,
            "end_frame": 1,
            "blender_version": self._blender_version_for_test,
            "render_engine": "CYCLES",
            "render_device": "GPU"
        }
        create_response = requests.post(f"{MANAGER_URL}/jobs/", json=job_payload)
        assert create_response.status_code == 201
        job_id = create_response.json()['id']
        job_url = f"{MANAGER_URL}/jobs/{job_id}/"

        print(f"Job submitted. Polling for completion at {job_url}...")
        final_job_data = poll_for_completion(job_url, timeout_seconds=240) # Allow more time for GPU scene

        print("Verifying final job data and outputs...")
        assert final_job_data['render_time_seconds'] > 0
        verify_image_output(final_job_data['output_file'])
        verify_image_output(final_job_data['thumbnail'])
        print("SUCCESS: Single-frame GPU render workflow completed and verified.")