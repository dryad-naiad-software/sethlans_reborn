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
# tests/e2e/test_tiled_workflow.py

import io
import os
import platform
import time

import pytest
import requests
from PIL import Image

from workers.constants import RenderSettings
from .shared_setup import BaseE2ETest, MANAGER_URL
from .utils import is_gpu_available


class TestTiledWorkflow(BaseE2ETest):
    """
    End-to-end tests for the tiled rendering workflow.
    """

    def test_tiled_render_workflow(self):
        is_ci = os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true"
        is_macos_in_ci = platform.system() == "Darwin" and is_ci

        if is_macos_in_ci or not is_gpu_available():
            pytest.skip("Skipping tiled GPU-implicit test on macOS CI or host with no GPU.")

        print("\n--- ACTION: Submitting tiled render job ---")
        tiled_job_payload = {
            "name": "E2E Tiled Render Test",
            "project": self.project_id,
            "asset_id": self.bmw_asset_id,
            "final_resolution_x": 400,
            "final_resolution_y": 400,
            "tile_count_x": 2,
            "tile_count_y": 2,
            "blender_version": "4.5",
            # FIX: Add required frame range for the job.
            "start_frame": 1,
            "end_frame": 1,
            "render_settings": {RenderSettings.SAMPLES: 32}
        }
        create_response = requests.post(f"{MANAGER_URL}/tiled-jobs/", json=tiled_job_payload)
        assert create_response.status_code == 201
        tiled_job_id = create_response.json()['id']
        tiled_job_url = f"{MANAGER_URL}/tiled-jobs/{tiled_job_id}/"

        print("Polling API for DONE status...")
        final_status = ""
        for i in range(180):
            check_response = requests.get(tiled_job_url)
            assert check_response.status_code == 200
            data = check_response.json()
            current_status = data['status']
            progress = data.get('progress', 'N/A')
            print(f"  Attempt {i + 1}/180: Current job status is {current_status} ({progress})")
            if current_status in ["DONE", "ERROR"]:
                final_status = current_status
                break
            time.sleep(2)
        assert final_status == "DONE"

        print("Verifying final job data, output file, and thumbnail...")
        final_job_data = requests.get(tiled_job_url).json()
        assert final_job_data['output_file'] is not None
        assert final_job_data['thumbnail'] is not None

        print("Downloading final assembled image...")
        output_url = final_job_data['output_file']
        output_res = requests.get(output_url)
        assert output_res.status_code == 200
        with Image.open(io.BytesIO(output_res.content)) as img:
            assert img.size == (400, 400)

        print("Downloading and verifying thumbnail...")
        thumb_url = final_job_data['thumbnail']
        thumb_res = requests.get(thumb_url)
        assert thumb_res.status_code == 200