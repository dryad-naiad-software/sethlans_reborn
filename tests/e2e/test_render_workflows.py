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
# Created by Mario Estrella on 7/31/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
# tests/e2e/test_render_workflows.py

import os
import platform
import time

import pytest
import requests

from workers.constants import RenderSettings
from .shared_setup import BaseE2ETest, MANAGER_URL
from .utils import is_gpu_available


class TestRenderWorkflow(BaseE2ETest):
    def test_full_render_workflow(self):
        print("\n--- ACTION: Submitting render job ---")
        job_payload = {
            "name": "E2E CPU Render Test",
            "asset_id": self.scene_asset_id,
            "output_file_pattern": "e2e_render_####",
            "start_frame": 1, "end_frame": 1, "blender_version": self._blender_version_for_test,
            "render_engine": "CYCLES",
            "render_device": "CPU",  # Explicitly test CPU
            "render_settings": {
                RenderSettings.SAMPLES: 10,
                RenderSettings.RESOLUTION_PERCENTAGE: 10
            }
        }
        create_response = requests.post(f"{MANAGER_URL}/jobs/", json=job_payload)
        assert create_response.status_code == 201
        job_id = create_response.json()['id']
        job_url = f"{MANAGER_URL}/jobs/{job_id}/"

        print("Polling API for DONE status...")
        final_status = ""
        for i in range(120):
            check_response = requests.get(job_url)
            if check_response.status_code == 200:
                current_status = check_response.json()['status']
                if current_status in ["DONE", "ERROR"]:
                    final_status = current_status
                    break
            time.sleep(2)
        assert final_status == "DONE"

        print("Verifying final job data and output file...")
        final_job_response = requests.get(job_url)
        assert final_job_response.status_code == 200
        final_job_data = final_job_response.json()

        assert final_job_data.get('render_time_seconds') is not None
        assert final_job_data.get('render_time_seconds') > 0
        assert 'output_file' in final_job_data
        output_url = final_job_data['output_file']
        assert output_url is not None

        print(f"Downloading output file from {output_url}...")
        download_response = requests.get(output_url)
        assert download_response.status_code == 200
        assert len(download_response.content) > 0, "Downloaded output file is empty."


class TestGpuWorkflow(BaseE2ETest):
    def test_full_gpu_render_workflow(self):
        is_ci = os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true"
        is_macos_in_ci = platform.system() == "Darwin" and is_ci

        if not is_gpu_available() or is_macos_in_ci:
            pytest.skip("Skipping GPU test: No GPU available or running in macOS CI environment.")

        print("\n--- ACTION: Submitting full GPU render job ---")
        job_payload = {
            "name": "E2E Full GPU Render Test",
            "asset_id": self.bmw_asset_id,
            "output_file_pattern": "e2e_gpu_render_####",
            "start_frame": 1, "end_frame": 1, "blender_version": self._blender_version_for_test,
            "render_engine": "CYCLES",
            "render_device": "GPU",
        }
        create_response = requests.post(f"{MANAGER_URL}/jobs/", json=job_payload)
        assert create_response.status_code == 201
        job_id = create_response.json()['id']

        print("Polling API for DONE status...")
        final_status = ""
        for i in range(120):
            check_response = requests.get(f"{MANAGER_URL}/jobs/{job_id}/")
            if check_response.status_code == 200:
                current_status = check_response.json()['status']
                print(f"  Attempt {i + 1}/120: Current job status is {current_status}")
                if current_status in ["DONE", "ERROR"]:
                    final_status = current_status
                    break
            time.sleep(2)
        assert final_status == "DONE", f"Job finished with status '{final_status}', expected 'DONE'."
        print("E2E Full GPU Render Test Passed!")