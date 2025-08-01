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
# tests/e2e/test_animation_workflows.py

import io
import os
import platform
import time

import pytest
import requests
from PIL import Image

from workers.constants import RenderSettings, TilingConfiguration
from workers.image_utils import THUMBNAIL_SIZE
from .shared_setup import BaseE2ETest, MANAGER_URL


class TestAnimationWorkflow(BaseE2ETest):
    def test_animation_render_workflow(self):
        start_frame, end_frame = 1, 5
        total_frames = (end_frame - start_frame) + 1
        output_pattern = "anim_render_####"

        print("\n--- ACTION: Submitting animation job ---")
        anim_payload = {
            "name": "E2E Animation Test",
            "project": self.project_id,
            "asset_id": self.anim_asset_id,
            "output_file_pattern": output_pattern,
            "start_frame": start_frame,
            "end_frame": end_frame,
            "blender_version": self._blender_version_for_test,
            "render_device": "CPU",
            "render_settings": {
                RenderSettings.SAMPLES: 25,
                RenderSettings.RESOLUTION_PERCENTAGE: 25
            }
        }
        create_response = requests.post(f"{MANAGER_URL}/animations/", json=anim_payload)
        assert create_response.status_code == 201
        anim_id = create_response.json()['id']
        anim_url = f"{MANAGER_URL}/animations/{anim_id}/"

        print(f"Polling API for completion of {total_frames} frames...")
        completed = False
        for i in range(150):
            check_response = requests.get(anim_url)
            assert check_response.status_code == 200
            data = check_response.json()
            completed_frames = data.get('completed_frames', 0)
            print(f"  Attempt {i + 1}/150: {data.get('progress', 'N/A')}")
            if completed_frames == total_frames:
                completed = True
                break
            time.sleep(2)
        assert completed, f"Animation did not complete in time. Only {completed_frames}/{total_frames} frames finished."

        print("Verifying all child jobs have an output file and thumbnail URL...")
        jobs_response = requests.get(f"{MANAGER_URL}/jobs/?animation={anim_id}")
        assert jobs_response.status_code == 200
        child_jobs = jobs_response.json()
        assert len(child_jobs) == total_frames

        for job in child_jobs:
            assert job.get('output_file') is not None, f"Job {job['id']} is missing its output file URL."
            assert job.get('thumbnail') is not None, f"Job {job['id']} is missing its thumbnail URL."

        print("Verifying parent animation has a thumbnail...")
        final_anim_data = requests.get(anim_url).json()
        assert final_anim_data.get('thumbnail') is not None
        thumb_url = final_anim_data['thumbnail']
        thumb_res = requests.get(thumb_url)
        assert thumb_res.status_code == 200
        with Image.open(io.BytesIO(thumb_res.content)) as img:
            assert img.width <= THUMBNAIL_SIZE[0]
            assert img.height <= THUMBNAIL_SIZE[1]

        print("SUCCESS: All animation frames were rendered and all thumbnails generated.")

    def test_animation_with_frame_step(self):
        start_frame, end_frame, frame_step = 1, 5, 2
        expected_frames = [1, 3, 5]
        expected_job_count = len(expected_frames)

        print("\n--- ACTION: Submitting animation job with frame_step ---")
        anim_payload = {
            "name": "E2E Frame Step Test",
            "project": self.project_id,
            "asset_id": self.anim_asset_id,
            "output_file_pattern": "frame_step_render_####",
            "start_frame": start_frame,
            "end_frame": end_frame,
            "frame_step": frame_step,
            "blender_version": self._blender_version_for_test,
        }
        create_response = requests.post(f"{MANAGER_URL}/animations/", json=anim_payload)
        assert create_response.status_code == 201
        anim_id = create_response.json()['id']
        anim_url = f"{MANAGER_URL}/animations/{anim_id}/"

        print(f"Polling API for completion of {expected_job_count} stepped frames...")
        completed = False
        for i in range(150):
            check_response = requests.get(anim_url)
            assert check_response.status_code == 200
            data = check_response.json()
            completed_frames = data.get('completed_frames', 0)
            if completed_frames == expected_job_count:
                completed = True
                break
            time.sleep(2)
        assert completed, f"Animation with frame step did not complete in time."

        jobs_response = requests.get(f"{MANAGER_URL}/jobs/?animation={anim_id}")
        assert jobs_response.status_code == 200
        child_jobs = jobs_response.json()
        assert len(child_jobs) == expected_job_count

        spawned_frames = sorted([job['start_frame'] for job in child_jobs])
        assert spawned_frames == expected_frames
        print(f"SUCCESS: Correctly spawned jobs for frames {spawned_frames}.")


class TestTiledAnimationWorkflow(BaseE2ETest):
    def test_tiled_animation_workflow(self):
        is_ci = os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true"
        is_macos_in_ci = platform.system() == "Darwin" and is_ci

        if is_macos_in_ci:
            pytest.skip("Skipping tiled animation GPU-implicit test on macOS CI to maintain stability.")

        print("\n--- ACTION: Submitting Tiled Animation job ---")
        start_frame, end_frame = 1, 2
        total_frames = (end_frame - start_frame) + 1
        anim_payload = {
            "name": "E2E Tiled Animation Test",
            "project": self.project_id,
            "asset_id": self.anim_asset_id,
            "output_file_pattern": "tiled_anim_e2e_####",
            "start_frame": start_frame,
            "end_frame": end_frame,
            "blender_version": self._blender_version_for_test,
            "tiling_config": TilingConfiguration.TILE_2X2,
            "render_settings": {
                RenderSettings.SAMPLES: 16,
                RenderSettings.RESOLUTION_X: 200,
                RenderSettings.RESOLUTION_Y: 200
            }
        }
        create_response = requests.post(f"{MANAGER_URL}/animations/", json=anim_payload)
        assert create_response.status_code == 201
        anim_id = create_response.json()['id']
        anim_url = f"{MANAGER_URL}/animations/{anim_id}/"

        print(f"Polling API for completion of {total_frames} frames...")
        final_status = ""
        for i in range(240):
            check_response = requests.get(anim_url)
            assert check_response.status_code == 200
            data = check_response.json()
            current_status = data['status']
            print(f"  Attempt {i + 1}/240: Animation status is {current_status} ({data.get('progress', 'N/A')})")
            if current_status in ["DONE", "ERROR"]:
                final_status = current_status
                break
            time.sleep(2)
        assert final_status == "DONE"

        print("Verifying final animation data and output files...")
        final_anim_response = requests.get(anim_url)
        assert final_anim_response.status_code == 200
        final_anim_data = final_anim_response.json()

        assert final_anim_data['thumbnail'] is not None
        assert len(final_anim_data['frames']) == total_frames

        for frame_data in final_anim_data['frames']:
            assert frame_data['status'] == 'DONE'
            assert frame_data['output_file'] is not None
            assert frame_data['thumbnail'] is not None
            print(f"Downloading and verifying assembled frame {frame_data['frame_number']}...")
            download_response = requests.get(frame_data['output_file'])
            assert download_response.status_code == 200
            with Image.open(io.BytesIO(download_response.content)) as img:
                assert img.size == (200, 200)

        print("SUCCESS: Tiled animation workflow and thumbnails completed successfully.")