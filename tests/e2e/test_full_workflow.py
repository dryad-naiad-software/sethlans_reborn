# sethlans_reborn/tests/e2e/test_full_workflow.py
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
# Created by Mario Estrella on 07/23/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#

import os
import shutil
import subprocess
import sys
import time
import platform
from pathlib import Path
import requests

from sethlans_worker_agent import config as worker_config

# --- Test Constants ---
MANAGER_URL = "http://127.0.0.1:8000/api"
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TEST_DB_NAME = "test_e2e_db.sqlite3"
MOCK_TOOLS_DIR = Path(worker_config.MANAGED_TOOLS_DIR)


class TestRenderWorkflow:
    """Groups tests for the standard render workflow."""
    manager_process = None
    worker_process = None

    def setup_class(cls):
        """Set up the environment for all tests in this class."""
        print("\n--- SETUP: TestRenderWorkflow ---")
        # Clean slate
        if os.path.exists(TEST_DB_NAME):
            os.remove(TEST_DB_NAME)
        if MOCK_TOOLS_DIR.exists():
            shutil.rmtree(MOCK_TOOLS_DIR, ignore_errors=True)
        if Path(worker_config.TEST_OUTPUT_DIR).exists():
            shutil.rmtree(worker_config.TEST_OUTPUT_DIR, ignore_errors=True)
        if os.path.exists(worker_config.BLENDER_VERSIONS_CACHE_FILE):
            os.remove(worker_config.BLENDER_VERSIONS_CACHE_FILE)

        # Prepare Django
        print("Running migrations...")
        test_env = os.environ.copy()
        test_env["SETHLANS_DB_NAME"] = TEST_DB_NAME
        subprocess.run([sys.executable, "manage.py", "migrate"], cwd=PROJECT_ROOT, env=test_env, check=True,
                       capture_output=True)

        # Start services
        print("Starting Django manager...")
        cls.manager_process = subprocess.Popen([sys.executable, "manage.py", "runserver", "--noreload"],
                                               cwd=PROJECT_ROOT, env=test_env, stdout=subprocess.DEVNULL,
                                               stderr=subprocess.DEVNULL)
        time.sleep(5)

        print("Starting Worker Agent...")
        worker_command = [sys.executable, "-m", "sethlans_worker_agent.agent", "--loglevel", "DEBUG"]
        cls.worker_process = subprocess.Popen(worker_command, cwd=PROJECT_ROOT, env=test_env, stdout=subprocess.PIPE,
                                              stderr=subprocess.STDOUT, text=True)
        time.sleep(5)

    def teardown_class(cls):
        """Tear down the environment after all tests in this class."""
        print("\n--- TEARDOWN: TestRenderWorkflow ---")
        if cls.worker_process:
            print(f"Stopping worker process tree with PID: {cls.worker_process.pid}...")
            if platform.system() == "Windows":
                subprocess.run(f"taskkill /F /T /PID {cls.worker_process.pid}", check=False, capture_output=True)
            else:
                cls.worker_process.kill()

        if cls.manager_process:
            print(f"Stopping manager process tree with PID: {cls.manager_process.pid}...")
            if platform.system() == "Windows":
                subprocess.run(f"taskkill /F /T /PID {cls.manager_process.pid}", check=False, capture_output=True)
            else:
                cls.manager_process.kill()

        # Final cleanup
        if os.path.exists(TEST_DB_NAME):
            os.remove(TEST_DB_NAME)
        if Path(worker_config.TEST_OUTPUT_DIR).exists():
            shutil.rmtree(worker_config.TEST_OUTPUT_DIR, ignore_errors=True)
        if MOCK_TOOLS_DIR.exists():
            shutil.rmtree(MOCK_TOOLS_DIR, ignore_errors=True)
        if os.path.exists(worker_config.BLENDER_VERSIONS_CACHE_FILE):
            os.remove(worker_config.BLENDER_VERSIONS_CACHE_FILE)
        print("Teardown complete.")

    def test_full_render_workflow(self):
        """
        Tests the full end-to-end render workflow.
        """
        print("\n--- ACTION: Submitting render job ---")
        job_payload = {
            "name": "E2E Monitored Test",
            "blend_file_path": worker_config.TEST_BLEND_FILE_PATH,
            "output_file_pattern": os.path.join(worker_config.TEST_OUTPUT_DIR, "e2e_render_####"),
            "start_frame": 1, "end_frame": 1, "blender_version": "4.1.1",
            "render_engine": "BLENDER_EEVEE",
            "render_settings": {}
        }
        create_response = requests.post(f"{MANAGER_URL}/jobs/", json=job_payload)
        assert create_response.status_code == 201, "Failed to create the job"
        job_id = create_response.json()['id']
        print(f"Job submitted successfully with ID: {job_id}")

        print("Waiting for worker to download, extract, and start rendering...")
        render_started = False
        max_wait_seconds = 300
        start_time = time.time()
        while time.time() - start_time < max_wait_seconds:
            line = self.worker_process.stdout.readline()
            if not line:
                time.sleep(1)
                continue
            print(f"  [WORKER LOG] {line.strip()}")
            if "Running Command:" in line:
                print("Worker has started the render command!")
                render_started = True
                break
        assert render_started, "Worker did not start the render within the time limit."

        print("Render started. Polling API for DONE status...")
        final_status = ""
        for i in range(120):
            check_response = requests.get(f"{MANAGER_URL}/jobs/{job_id}/")
            if check_response.status_code == 200:
                current_status = check_response.json()['status']
                print(f"  Attempt {i + 1}/120: Current job status is {current_status}")
                if current_status in ["DONE", "ERROR"]:
                    final_status = current_status
                    break
            time.sleep(2)

        assert final_status == "DONE", f"Job finished with status '{final_status}', expected 'DONE'."
        print("E2E Render Test Passed!")