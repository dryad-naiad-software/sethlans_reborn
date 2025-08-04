# FILENAME: tests/e2e/test_animation_workflows.py
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
# tests/e2e/test_animation_workflows.py
"""
End-to-end tests for multi-frame animation workflows. 🎞️
"""

import requests
from .shared_setup import BaseE2ETest, MANAGER_URL
from .helpers import poll_for_completion, verify_image_output
from workers.constants import RenderSettings


class TestAnimationWorkflows(BaseE2ETest):
    """
    Validates standard animation rendering and features like frame stepping.
    """

    def test_standard_animation_render(self):
        """
        Tests the complete lifecycle of a standard multi-frame animation.

        It verifies that a parent Animation object is created, child Job objects
        are spawned for each frame, and the final status and thumbnail are correct.
        """
        print("\n--- E2E TEST: Standard Animation Render ---")
        start_frame, end_frame = 1, 3
        total_frames = (end_frame - start_frame) + 1

        anim_payload = {
            "name": f"E2E Animation Test {self.project_id}",
            "project": self.project_id,
            "asset_id": self.anim_asset_id,
            "output_file_pattern": "anim_render_####",
            "start_frame": start_frame,
            "end_frame": end_frame,
            "blender_version": self._blender_version_for_test,
            "render_settings": {
                RenderSettings.SAMPLES: 16,
                RenderSettings.RESOLUTION_X: 640,
                RenderSettings.RESOLUTION_Y: 360,
            }
        }
        create_response = requests.post(f"{MANAGER_URL}/animations/", json=anim_payload)
        assert create_response.status_code == 201, f"Failed to create animation: {create_response.text}"
        anim_id = create_response.json()['id']
        anim_url = f"{MANAGER_URL}/animations/{anim_id}/"

        print(f"Animation submitted. Polling for completion at {anim_url}...")
        final_anim_data = poll_for_completion(anim_url, timeout_seconds=180)

        print("Verifying final animation data...")
        assert final_anim_data['total_frames'] == total_frames
        assert final_anim_data['completed_frames'] == total_frames
        assert final_anim_data['total_render_time_seconds'] > 0
        verify_image_output(final_anim_data['thumbnail'])

        print("Verifying child jobs...")
        jobs_response = requests.get(f"{MANAGER_URL}/jobs/?animation={anim_id}")
        assert jobs_response.status_code == 200
        child_jobs = jobs_response.json()
        assert len(child_jobs) == total_frames
        for job in child_jobs:
            assert job['status'] == 'DONE'
            assert job['output_file'] is not None
            assert job['thumbnail'] is not None

        print("SUCCESS: Standard animation workflow completed and verified.")

    def test_animation_with_frame_step(self):
        """
        Tests that an animation with a `frame_step` > 1 spawns the correct
        number of child jobs for the correct frames.
        """
        print("\n--- E2E TEST: Animation with Frame Step ---")
        start_frame, end_frame, frame_step = 1, 5, 2
        expected_frames = [1, 3, 5]
        expected_job_count = len(expected_frames)

        anim_payload = {
            "name": f"E2E Frame Step Test {self.project_id}",
            "project": self.project_id,
            "asset_id": self.anim_asset_id,
            "output_file_pattern": "frame_step_render_####",
            "start_frame": start_frame,
            "end_frame": end_frame,
            "frame_step": frame_step,
            "blender_version": self._blender_version_for_test,
        }
        create_response = requests.post(f"{MANAGER_URL}/animations/", json=anim_payload)
        assert create_response.status_code == 201, f"Failed to create animation: {create_response.text}"
        anim_id = create_response.json()['id']
        anim_url = f"{MANAGER_URL}/animations/{anim_id}/"

        print(f"Animation submitted. Polling for completion at {anim_url}...")
        poll_for_completion(anim_url, timeout_seconds=180)

        print("Verifying child jobs for frame step...")
        jobs_response = requests.get(f"{MANAGER_URL}/jobs/?animation={anim_id}")
        assert jobs_response.status_code == 200
        child_jobs = jobs_response.json()
        assert len(child_jobs) == expected_job_count

        spawned_frames = sorted([job['start_frame'] for job in child_jobs])
        assert spawned_frames == expected_frames
        print(f"SUCCESS: Correctly spawned jobs for frames {spawned_frames}.")