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

import io
import os
import platform
import time

import pytest
import requests
from PIL import Image

from workers.constants import RenderSettings
from workers.image_utils import THUMBNAIL_SIZE
from .utils import is_gpu_available
from .shared_setup import BaseE2ETest, MANAGER_URL


class TestRenderWorkflow(BaseE2ETest):
    """
    End-to-end tests for the basic single-frame render workflow.
    """

    def test_full_render_workflow(self):
        print("\n--- ACTION: Submitting render job ---")
        job_payload = {
            "name": f"E2E CPU Render Test {self.project_id}",
            "project": self.project_id,
            "asset_id": self.scene_asset_id,
            "output_file_pattern": "e2e_render_####",
            "start_frame": 1, "end_frame": 1, "blender_version": self._blender_version_for_test,
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

        print("Polling API for DONE status...")
        final_status = ""
        for i in range(120):
            check_response = requests.get(job_url)
            assert check_response.status_code == 200
            current_status = check_response.json()['status']
            if current_status in ["DONE", "ERROR"]:
                final_status = current_status
                break
            time.sleep(2)

        assert final_status == "DONE"

        print("Verifying final job data, output file, and thumbnail...")
        final_job_data = requests.get(job_url).json()
        assert final_job_data['output_file'] is not None
        assert final_job_data['thumbnail'] is not None
        assert final_job_data['render_time_seconds'] > 0

        print("Downloading and verifying output file...")
        output_url = final_job_data['output_file']
        output_res = requests.get(output_url)
        assert output_res.status_code == 200

        print("Downloading and verifying thumbnail...")
        thumb_url = final_job_data['thumbnail']
        thumb_res = requests.get(thumb_url)
        assert thumb_res.status_code == 200
        with Image.open(io.BytesIO(thumb_res.content)) as img:
            assert img.width <= THUMBNAIL_SIZE[0]
            assert img.height <= THUMBNAIL_SIZE[1]

            # FIX: Replace call to deleted function with a direct check for image brightness.
            # Get the min/max brightness of the image (0=black, 255=white).
            min_val, max_val = img.convert('L').getextrema()
            assert max_val > 10, "Thumbnail image is unexpectedly dark or completely black."


class TestGpuWorkflow(BaseE2ETest):
    """
    End-to-end tests for the GPU single-frame render workflow.
    """

    def test_full_gpu_render_workflow(self):
        is_ci = os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true"
        is_macos_in_ci = platform.system() == "Darwin" and is_ci

        if not is_gpu_available() or is_macos_in_ci:
            pytest.skip("Skipping GPU test: No GPU available or running in macOS CI environment.")

        print("\n--- ACTION: Submitting full GPU render job ---")
        job_payload = {
            "name": f"E2E Full GPU Render Test {self.project_id}",
            "project": self.project_id,
            "asset_id": self.bmw_asset_id,
            "output_file_pattern": "e2e_gpu_render_####",
            "start_frame": 1, "end_frame": 1, "blender_version": self._blender_version_for_test,
            "render_engine": "CYCLES",
            "render_device": "GPU",
        }
        create_response = requests.post(f"{MANAGER_URL}/jobs/", json=job_payload)
        assert create_response.status_code == 201
        job_id = create_response.json()['id']
        job_url = f"{MANAGER_URL}/jobs/{job_id}/"

        print("Polling API for DONE status...")
        final_status = ""
        for i in range(120):  # 4 minutes timeout
            check_response = requests.get(job_url)
            assert check_response.status_code == 200
            data = check_response.json()
            current_status = data['status']
            print(f"  Attempt {i + 1}/120: Current job status is {current_status}")
            if current_status in ["DONE", "ERROR"]:
                final_status = current_status
                break
            time.sleep(2)

        assert final_status == "DONE"
        print("E2E Full GPU Render Test Passed!")