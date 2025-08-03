# tests/e2e/test_manifests.py
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
# Created by Gemini on 8/2/2025.
# Dryad and Naiad Software LLC
#
# Project: sethlans_reborn
#
"""
End-to-end tests for the automatic project manifest generation. 📝
"""
import time
from pathlib import Path

import requests

from .shared_setup import BaseE2ETest, MANAGER_URL, MEDIA_ROOT_FOR_TEST


class TestManifestCreation(BaseE2ETest):
    """
    Validates that manifest.txt files are created and updated correctly
    in response to API actions.
    """

    def test_manifest_is_created_and_updated(self):
        """
        Tests that a manifest.txt is created when a project is created, and
        is subsequently updated when a new job is added via the API.
        """
        print("\n--- E2E TEST: Project Manifest Creation and Update ---")

        # 1. The setup_class already created a project. Check for the initial manifest.
        manifest_path = MEDIA_ROOT_FOR_TEST / 'assets' / str(self.project_id) / 'manifest.txt'
        assert manifest_path.exists(), "Manifest file was not created after project setup."

        initial_content = manifest_path.read_text()
        print("Initial manifest found. Verifying content...")
        assert f"Project Name: E2E-Test-Project-" in initial_content
        assert "No jobs found for this project." in initial_content
        assert self.scene_asset_id is not None
        assert f"E2E Test Scene" in initial_content # Checks for asset name

        # 2. Create a new job via the API, which should trigger an update.
        print("Submitting a new job to trigger manifest update...")
        job_payload = {
            "name": f"E2E Manifest Test Job {int(time.time())}",
            "asset_id": self.scene_asset_id,
            "output_file_pattern": "manifest_test_####",
            "start_frame": 1, "end_frame": 1,
            "blender_version": self._blender_version_for_test
        }
        create_response = requests.post(f"{MANAGER_URL}/jobs/", json=job_payload)
        assert create_response.status_code == 201

        # 3. Verify the manifest was updated with the new job.
        # A small delay might be needed for the signal handler to write the file.
        time.sleep(0.5)
        updated_content = manifest_path.read_text()
        print("Verifying updated manifest content...")

        assert updated_content != initial_content
        assert "No jobs found for this project." not in updated_content
        assert "[Job] E2E Manifest Test Job" in updated_content
        assert "Asset: E2E Test Scene" in updated_content

        print("SUCCESS: Project manifest was created and updated correctly.")