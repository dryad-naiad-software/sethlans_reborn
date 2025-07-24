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
# Created by Mario Estrella on 7/24/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#


# tests/e2e/test_full_workflow.py
import pytest
import subprocess
import time
import requests
import os
import sys
from pathlib import Path

from sethlans_worker_agent import config as worker_config

# Define constants for the test
MANAGER_URL = "http://127.0.0.1:8000/api"
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
TEST_DB_NAME = "test_e2e_db.sqlite3"


def test_full_render_workflow():
    """
    Tests the full end-to-end workflow using a mock Blender executable.
    """
    manager_process = None
    worker_process = None

    # --- Define paths for our mock Blender setup ---
    blender_version = "4.1.1"
    platform_details = worker_config.CURRENT_PLATFORM_BLENDER_DETAILS
    platform_suffix = platform_details['download_suffix']
    exe_path_in_folder = platform_details['executable_path_in_folder']

    mock_blender_folder = Path(
        worker_config.MANAGED_TOOLS_DIR) / "blender" / f"blender-{blender_version}-{platform_suffix}"
    mock_blender_exe = mock_blender_folder / exe_path_in_folder

    test_env = os.environ.copy()
    test_env["SETHLANS_DB_NAME"] = TEST_DB_NAME

    try:
        # --- 0. SETUP: Create mock Blender and run migrations ---
        print("\nSetting up mock Blender executable...")
        mock_blender_folder.mkdir(parents=True, exist_ok=True)

        # Create a simple script that acts as the blender executable
        if sys.platform == "win32":
            # Windows can execute a .bat script even if it's named .exe
            mock_blender_exe.write_text("@echo off\nexit 0")
        else:
            mock_blender_exe.write_text("#!/bin/bash\nexit 0")
            mock_blender_exe.chmod(0o755)

        if os.path.exists(TEST_DB_NAME):
            os.remove(TEST_DB_NAME)

        print("Running migrations on test database...")
        migrate_command = [sys.executable, "manage.py", "migrate"]
        subprocess.run(migrate_command, cwd=PROJECT_ROOT, env=test_env, check=True)

        # --- 1. START SERVICES ---
        print("Starting Django manager...")
        manager_command = [sys.executable, "manage.py", "runserver", "--noreload"]
        manager_process = subprocess.Popen(manager_command, cwd=PROJECT_ROOT, env=test_env)
        time.sleep(5)

        print("Starting Worker Agent...")
        worker_command = [sys.executable, "-m", "sethlans_worker_agent.agent"]
        worker_process = subprocess.Popen(worker_command, cwd=PROJECT_ROOT, env=test_env)
        time.sleep(5)

        # --- 2. ACTION: Submit Job ---
        print("Submitting new render job via API...")
        job_payload = {
            "name": "E2E Test Job",
            "blend_file_path": os.path.join(PROJECT_ROOT, "test_scene.blend"),
            "output_file_pattern": os.path.join(PROJECT_ROOT, "test_output", "e2e_render_####"),
            "start_frame": 1,
            "end_frame": 1,
            "blender_version": blender_version
        }
        create_response = requests.post(f"{MANAGER_URL}/jobs/", json=job_payload)
        assert create_response.status_code == 201
        job_id = create_response.json()['id']
        print(f"Job submitted successfully with ID: {job_id}")

        # --- 3. VERIFICATION ---
        print("Waiting for worker to complete the job (up to 20 seconds)...")
        final_status = ""
        for _ in range(10):
            time.sleep(2)
            try:
                check_response = requests.get(f"{MANAGER_URL}/jobs/{job_id}/")
                if check_response.status_code == 200:
                    current_status = check_response.json()['status']
                    print(f"  Current job status: {current_status}")
                    if current_status == "DONE":
                        final_status = current_status
                        break
            except requests.ConnectionError:
                continue

        assert final_status == "DONE", "Job did not complete successfully in time."
        print("E2E Test Passed!")

    finally:
        # --- 4. TEARDOWN ---
        print("Tearing down processes...")
        if worker_process:
            worker_process.terminate()
        if manager_process:
            manager_process.terminate()

        if worker_process:
            worker_process.wait()
        if manager_process:
            manager_process.wait()

        if os.path.exists(TEST_DB_NAME):
            os.remove(TEST_DB_NAME)

        # Clean up the mock blender files and dirs
        if mock_blender_exe.exists():
            mock_blender_exe.unlink()
        if mock_blender_folder.exists():
            mock_blender_folder.rmdir()

        print("Teardown complete.")