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

# Define constants for the test
MANAGER_URL = "http://127.0.0.1:8000/api"
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))


def test_full_render_workflow():
    """
    Tests the full end-to-end workflow:
    1. Starts the manager and worker.
    2. Submits a job via the API.
    3. Waits for the worker to complete the job.
    4. Verifies the job status is 'DONE'.
    """
    manager_process = None
    worker_process = None

    try:
        # --- 1. SETUP: Start Server and Worker ---
        print("\nStarting Django manager...")
        # Start manager. Use --noreload to keep it in a single process for stability.
        manager_command = [sys.executable, "manage.py", "runserver", "--noreload"]
        manager_process = subprocess.Popen(manager_command, cwd=PROJECT_ROOT)

        # Wait a few seconds for the manager to start up
        time.sleep(5)

        print("Starting Worker Agent...")
        worker_command = [sys.executable, "-m", "sethlans_worker_agent.agent"]
        worker_process = subprocess.Popen(worker_command, cwd=PROJECT_ROOT)

        # Allow worker time to register
        time.sleep(5)

        # --- 2. ACTION: Submit a new job ---
        print("Submitting new render job via API...")
        job_payload = {
            "name": "E2E Test Job",
            "blend_file_path": os.path.join(PROJECT_ROOT, "test_scene.blend"),
            "output_file_pattern": os.path.join(PROJECT_ROOT, "test_output", "e2e_render_####"),
            "start_frame": 1,
            "end_frame": 1
        }
        create_response = requests.post(f"{MANAGER_URL}/jobs/", json=job_payload)
        assert create_response.status_code == 201
        job_id = create_response.json()['id']
        print(f"Job submitted successfully with ID: {job_id}")

        # --- 3. VERIFICATION: Wait and check job status ---
        print("Waiting for worker to complete the job (up to 20 seconds)...")
        final_status = ""
        for _ in range(10):  # Poll for 20 seconds (10 * 2s)
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
                print("  Manager not ready yet...")
                continue

        assert final_status == "DONE", "Job did not complete successfully in time."
        print("E2E Test Passed!")

    finally:
        # --- 4. TEARDOWN: Stop processes ---
        print("Tearing down processes...")
        if worker_process:
            worker_process.terminate()
        if manager_process:
            manager_process.terminate()

        # Wait for processes to fully close
        if worker_process:
            worker_process.wait()
        if manager_process:
            manager_process.wait()
        print("Teardown complete.")