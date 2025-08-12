# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
#
# Created by Mario Estrella on 8/1/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
# tests/e2e/test_admin_features.py
"""
End-to-end tests for administrative features like pausing projects. ⏸️
"""
import time
import requests

from .shared_setup import BaseE2ETest, MANAGER_URL
from .helpers import poll_for_completion
from sethlans_worker_agent import config as worker_config


class TestAdminFeatures(BaseE2ETest):
    """
    Validates administrative actions and their effect on the render queue.
    """

    def test_project_pause_workflow(self):
        """
        Tests that a worker correctly ignores jobs from a paused project and
        resumes processing them once the project is unpaused.
        """
        print("\n--- E2E TEST: Project Pause and Resume Workflow ---")

        # 1. Create a new, separate project to be paused
        unique_suffix = int(time.time())
        paused_project_payload = {"name": f"E2E Paused Project {unique_suffix}"}
        create_proj_res = requests.post(f"{MANAGER_URL}/projects/", json=paused_project_payload)
        assert create_proj_res.status_code == 201
        paused_project_id = create_proj_res.json()['id']
        print(f"Created a separate project to pause (ID: {paused_project_id})")

        paused_asset_id = self._upload_test_asset(
            f"Paused Project Asset {unique_suffix}",
            worker_config.TEST_BLEND_FILE_PATH,
            paused_project_id
        )

        # 2. Submit a job to the standard (active) project and the new (soon-to-be-paused) project
        active_job_payload = {
            "name": f"E2E Active Project Job {unique_suffix}",
            "project": self.project_id,
            "asset_id": self.scene_asset_id,
            "output_file_pattern": "active_job_####",
            "start_frame": 1, "end_frame": 1,
            "blender_version": self._blender_version_for_test
        }
        active_job_res = requests.post(f"{MANAGER_URL}/jobs/", json=active_job_payload)
        assert active_job_res.status_code == 201
        active_job_url = f"{MANAGER_URL}/jobs/{active_job_res.json()['id']}/"

        paused_job_payload = {
            "name": f"E2E Paused Project Job {unique_suffix}",
            "project": paused_project_id,
            "asset_id": paused_asset_id,
            "output_file_pattern": "paused_job_####",
            "start_frame": 1, "end_frame": 1,
            "blender_version": self._blender_version_for_test
        }
        paused_job_res = requests.post(f"{MANAGER_URL}/jobs/", json=paused_job_payload)
        assert paused_job_res.status_code == 201
        paused_job_url = f"{MANAGER_URL}/jobs/{paused_job_res.json()['id']}/"

        # 3. Pause the new project
        print(f"Pausing project {paused_project_id}...")
        pause_res = requests.post(f"{MANAGER_URL}/projects/{paused_project_id}/pause/")
        assert pause_res.status_code == 200

        # 4. Verify the active job completes while the paused job does not
        print("Polling for active job completion...")
        poll_for_completion(active_job_url)
        print("Active job completed as expected.")

        paused_job_status = requests.get(paused_job_url).json()['status']
        assert paused_job_status == 'QUEUED', f"Paused job was processed; status is {paused_job_status}"
        print("Job in paused project was correctly ignored by the worker.")

        # 5. Unpause the project and verify the job now completes
        print(f"Unpausing project {paused_project_id}...")
        unpause_res = requests.post(f"{MANAGER_URL}/projects/{paused_project_id}/unpause/")
        assert unpause_res.status_code == 200

        print("Polling for previously-paused job completion...")
        poll_for_completion(paused_job_url)
        print("Previously-paused job completed after project was unpaused.")

        print("SUCCESS: Project pause and resume workflow verified.")