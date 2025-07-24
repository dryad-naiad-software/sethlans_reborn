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
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests

from sethlans_worker_agent import config as worker_config

# --- Test Constants ---
MANAGER_URL = "http://127.0.0.1:8000/api"
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
TEST_DB_NAME = "test_e2e_db.sqlite3"
MOCK_TOOLS_DIR = Path(worker_config.MANAGED_TOOLS_DIR)


# In tests/e2e/test_full_workflow.py

def test_full_render_workflow():
    """
    Tests the full end-to-end workflow by actively monitoring the worker's
    log output for progress before verifying the final job status.
    """
    manager_process = None
    worker_process = None

    test_env = os.environ.copy()
    test_env["SETHLANS_DB_NAME"] = TEST_DB_NAME

    try:
        # =================================================================
        # 1. SETUP
        # =================================================================
        print("\n--- SETUP ---")
        if os.path.exists(TEST_DB_NAME):
            os.remove(TEST_DB_NAME)
        if os.path.exists(worker_config.BLENDER_VERSIONS_CACHE_FILE):
            os.remove(worker_config.BLENDER_VERSIONS_CACHE_FILE)

        managed_blender_dir = Path(worker_config.MANAGED_TOOLS_DIR) / "blender"
        if managed_blender_dir.exists():
            shutil.rmtree(managed_blender_dir)

        print("Running migrations on test database...")
        subprocess.run([sys.executable, "manage.py", "migrate"], cwd=PROJECT_ROOT, env=test_env, check=True,
                       capture_output=True)

        # =================================================================
        # 2. START SERVICES
        # =================================================================
        print("Starting Django manager...")
        manager_process = subprocess.Popen([sys.executable, "manage.py", "runserver", "--noreload"], cwd=PROJECT_ROOT,
                                           env=test_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(5)

        print("Starting Worker Agent...")
        # --- MODIFIED: Capture stdout and merge stderr ---
        worker_command = [sys.executable, "-m", "sethlans_worker_agent.agent", "--loglevel", "DEBUG"]
        worker_process = subprocess.Popen(
            worker_command,
            cwd=PROJECT_ROOT,
            env=test_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        time.sleep(5)

        # =================================================================
        # 3. ACTION: Submit the job
        # =================================================================
        print("Submitting new render job via API...")
        job_payload = {
            "name": "E2E Monitored Test",
            "blend_file_path": worker_config.TEST_BLEND_FILE_PATH,
            "output_file_pattern": os.path.join(worker_config.TEST_OUTPUT_DIR, "e2e_render_####"),
            "start_frame": 1,
            "end_frame": 1,
            "blender_version": "4.1.1"
        }
        create_response = requests.post(f"{MANAGER_URL}/jobs/", json=job_payload)
        assert create_response.status_code == 201, "Failed to create the job"
        job_id = create_response.json()['id']
        print(f"Job submitted successfully with ID: {job_id}")

        # =================================================================
        # 4. VERIFICATION: Monitor logs, then poll API
        # =================================================================
        print("Waiting for worker to download and start rendering...")

        # Define the sequence of logs we expect to see for a first-time run
        expected_log_stages = {
            "DOWNLOAD": "Downloading http",
            "EXTRACT": "Extracting",
            "RENDER": "Running Blender command"
        }
        found_stages = {key: False for key in expected_log_stages}

        # Increased timeout to 5 minutes (300 seconds) to accommodate slow downloads
        max_wait_seconds = 300
        start_time = time.time()

        # Monitor the worker's stdout in a single, more robust loop
        while time.time() - start_time < max_wait_seconds:
            line = worker_process.stdout.readline()
            if not line:
                time.sleep(1)
                continue

            print(f"  [WORKER LOG] {line.strip()}")

            # Check for each stage
            for stage_name, log_text in expected_log_stages.items():
                if not found_stages[stage_name] and log_text in line:
                    print(f"✅ Worker has reached stage: {stage_name}")
                    found_stages[stage_name] = True

            # If all stages are found, we can break early
            if all(found_stages.values()):
                break

        # Assert that the full first-time setup and render launch was successful
        assert all(found_stages.values()), f"Worker did not complete all expected stages. Status: {found_stages}"

        # Stage 2: Now that the render has started, poll for the DONE status
        print("Render started. Polling API for DONE status...")
        final_status = ""
        for i in range(15):  # Max wait of 30 seconds for the actual render
            check_response = requests.get(f"{MANAGER_URL}/jobs/{job_id}/")
            if check_response.status_code == 200:
                current_status = check_response.json()['status']
                print(f"  Attempt {i + 1}/15: Current job status is {current_status}")
                if current_status in ["DONE", "ERROR"]:
                    final_status = current_status
                    break
            time.sleep(2)

        assert final_status == "DONE", f"Job finished with status '{final_status}', expected 'DONE'."
        print("✅ E2E Test Passed!")
    finally:
        # =================================================================
        # 5. TEARDOWN
        # =================================================================
        print("--- TEARDOWN ---")
        if worker_process:
            print("Stopping worker process...")
            if worker_process.stdout:
                worker_process.stdout.close()
            worker_process.terminate()
            try:
                worker_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                print("Worker process did not terminate gracefully, killing.")
                worker_process.kill()

        if manager_process:
            print("Stopping manager process...")
            manager_process.terminate()
            try:
                manager_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                print("Manager process did not terminate gracefully, killing.")
                manager_process.kill()

        # Cleanup generated files and directories
        if os.path.exists(TEST_DB_NAME):
            print(f"Removing test database: {TEST_DB_NAME}")
            os.remove(TEST_DB_NAME)

        test_output_dir = Path(worker_config.TEST_OUTPUT_DIR)
        if test_output_dir.exists():
            print(f"Removing test output directory: {test_output_dir}")
            shutil.rmtree(test_output_dir, ignore_errors=True)

        if MOCK_TOOLS_DIR.exists():
            print(f"Removing managed tools directory: {MOCK_TOOLS_DIR}")
            shutil.rmtree(MOCK_TOOLS_DIR, ignore_errors=True)

        # Also remove the cache file in case it wasn't in the managed_tools dir
        if os.path.exists(worker_config.BLENDER_VERSIONS_CACHE_FILE):
            print(f"Removing Blender versions cache file: {worker_config.BLENDER_VERSIONS_CACHE_FILE}")
            os.remove(worker_config.BLENDER_VERSIONS_CACHE_FILE)

        print("Teardown complete.")
