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
# Created by Gemini on 8/1/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
# tests/e2e/test_tiled_workflows.py
"""
End-to-end tests for tiled rendering workflows. 🖼️
"""
import os
import platform
import pytest
import requests

from .shared_setup import BaseE2ETest, MANAGER_URL
from .helpers import poll_for_completion, verify_image_output, is_gpu_available
from workers.constants import RenderSettings


class TestTiledWorkflows(BaseE2ETest):
    """
    Validates the creation, execution, and automated assembly of tiled render jobs.
    """

    @pytest.mark.skipif(
        not is_gpu_available() or (platform.system() == "Darwin" and "CI" in os.environ),
        reason="Tiled rendering is intensive; skipped on hosts without a GPU or on unstable macOS CI"
    )
    def test_single_frame_tiled_render(self):
        """
        Tests the complete lifecycle of a single-frame tiled render job.

        This validates that a parent TiledJob spawns the correct number of
        child tile jobs, that those jobs are rendered, and that the final
        image is correctly assembled and has the requested dimensions.
        """
        print("\n--- E2E TEST: Single-Frame Tiled Render ---")
        resolution = (400, 300)
        tile_grid = (2, 2)
        total_tiles = tile_grid[0] * tile_grid[1]

        tiled_job_payload = {
            "name": f"E2E Tiled Render Test {self.project_id}",
            "project": self.project_id,
            "asset_id": self.bmw_asset_id,
            "final_resolution_x": resolution[0],
            "final_resolution_y": resolution[1],
            "tile_count_x": tile_grid[0],
            "tile_count_y": tile_grid[1],
            "blender_version": self._blender_version_for_test,
            "render_settings": {RenderSettings.SAMPLES: 32}
        }
        create_response = requests.post(f"{MANAGER_URL}/tiled-jobs/", json=tiled_job_payload)
        assert create_response.status_code == 201, f"Failed to create tiled job: {create_response.text}"
        tiled_job_id = create_response.json()['id']
        tiled_job_url = f"{MANAGER_URL}/tiled-jobs/{tiled_job_id}/"

        print(f"Tiled job submitted. Polling for completion at {tiled_job_url}...")
        final_tiled_job_data = poll_for_completion(tiled_job_url, timeout_seconds=300)

        print("Verifying final tiled job data and assembled output...")
        assert final_tiled_job_data['total_tiles'] == total_tiles
        assert final_tiled_job_data['completed_tiles'] == total_tiles
        assert final_tiled_job_data['total_render_time_seconds'] > 0

        # Verify the final assembled image and its thumbnail
        verify_image_output(final_tiled_job_data['output_file'], expected_size=resolution)
        verify_image_output(final_tiled_job_data['thumbnail'])

        print("Verifying child tile jobs...")
        jobs_response = requests.get(f"{MANAGER_URL}/jobs/?tiled_job={tiled_job_id}")
        assert jobs_response.status_code == 200
        child_jobs = jobs_response.json()
        assert len(child_jobs) == total_tiles
        for job in child_jobs:
            assert job['status'] == 'DONE'

        print("SUCCESS: Tiled render workflow completed and verified.")