# tests/e2e/test_full_workflow.py

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
import socket
from pathlib import Path
import pytest
import requests
import threading
import queue

from sethlans_worker_agent import config as worker_config
from sethlans_worker_agent import system_monitor


def is_gpu_available():
    """Checks if a compatible GPU is available for rendering."""
    devices = system_monitor.detect_gpu_devices()
    return len(devices) > 0


# --- Test Constants ---
MANAGER_URL = "http://127.0.0.1:8000/api"
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TEST_DB_NAME = "test_e2e_db.sqlite3"
MOCK_TOOLS_DIR = Path(worker_config.MANAGED_TOOLS_DIR)


class BaseE2ETest:
    manager_process = None
    worker_process = None
    worker_log_thread = None
    worker_log_queue = queue.Queue()

    @classmethod
    def _log_reader_thread(cls, pipe):
        """Reads lines from the worker's output pipe and puts them in a queue."""
        try:
            for line in iter(pipe.readline, ''):
                cls.worker_log_queue.put(line)
        finally:
            pipe.close()

    @classmethod
    def setup_class(cls):
        """Set up the environment for all tests in this class."""
        print(f"\n--- SETUP: {cls.__name__} ---")
        if os.path.exists(TEST_DB_NAME):
            os.remove(TEST_DB_NAME)
        if MOCK_TOOLS_DIR.exists():
            shutil.rmtree(MOCK_TOOLS_DIR, ignore_errors=True)
        if Path(worker_config.TEST_OUTPUT_DIR).exists():
            shutil.rmtree(worker_config.TEST_OUTPUT_DIR, ignore_errors=True)
        if os.path.exists(worker_config.BLENDER_VERSIONS_CACHE_FILE):
            os.remove(worker_config.BLENDER_VERSIONS_CACHE_FILE)

        print("Running migrations...")
        test_env = os.environ.copy()
        test_env["SETHLANS_DB_NAME"] = TEST_DB_NAME
        subprocess.run([sys.executable, "manage.py", "migrate"], cwd=PROJECT_ROOT, env=test_env, check=True,
                       capture_output=True)

        print("Starting Django manager...")
        cls.manager_process = subprocess.Popen([sys.executable, "manage.py", "runserver", "--noreload"],
                                               cwd=PROJECT_ROOT, env=test_env, stdout=subprocess.DEVNULL,
                                               stderr=subprocess.DEVNULL)
        time.sleep(5)

        print("Starting Worker Agent (will download Blender on first run)...")
        worker_command = [sys.executable, "-m", "sethlans_worker_agent.agent", "--loglevel", "DEBUG"]
        cls.worker_process = subprocess.Popen(worker_command, cwd=PROJECT_ROOT, env=test_env, stdout=subprocess.PIPE,
                                              stderr=subprocess.STDOUT, text=True)

        cls.worker_log_thread = threading.Thread(target=cls._log_reader_thread, args=(cls.worker_process.stdout,))
        cls.worker_log_thread.daemon = True
        cls.worker_log_thread.start()

        worker_ready = False
        max_wait_seconds = 420
        start_time = time.time()
        print("Waiting for worker to complete registration and initial setup...")
        while time.time() - start_time < max_wait_seconds:
            try:
                line = cls.worker_log_queue.get(timeout=1)
                print(f"  [SETUP LOG] {line.strip()}")
                if "Loop finished. Sleeping for" in line:
                    print("Worker is ready!")
                    worker_ready = True
                    break
            except queue.Empty:
                if cls.worker_process.poll() is not None:
                    raise RuntimeError("Worker process terminated unexpectedly during setup.")
                continue

        if not worker_ready:
            raise RuntimeError("Worker agent did not become ready within the time limit.")

    @classmethod
    def teardown_class(cls):
        """Tear down the environment after all tests in this class."""
        print(f"\n--- TEARDOWN: {cls.__name__} ---")

        print("\n--- CAPTURED WORKER LOGS ---")
        while not cls.worker_log_queue.empty():
            try:
                line = cls.worker_log_queue.get_nowait()
                print(f"  [WORKER] {line.strip()}")
            except queue.Empty:
                break
        print("--- END OF WORKER LOGS ---\n")

        if cls.worker_process:
            if platform.system() == "Windows":
                subprocess.run(f"taskkill /F /T /PID {cls.worker_process.pid}", check=False, capture_output=True)
            else:
                cls.worker_process.kill()

        if cls.manager_process:
            if platform.system() == "Windows":
                subprocess.run(f"taskkill /F /T /PID {cls.manager_process.pid}", check=False, capture_output=True)
            else:
                cls.manager_process.kill()

        if cls.worker_log_thread:
            cls.worker_log_thread.join(timeout=5)

        if os.path.exists(TEST_DB_NAME):
            os.remove(TEST_DB_NAME)
        if Path(worker_config.TEST_OUTPUT_DIR).exists():
            shutil.rmtree(worker_config.TEST_OUTPUT_DIR, ignore_errors=True)
        if MOCK_TOOLS_DIR.exists():
            shutil.rmtree(MOCK_TOOLS_DIR, ignore_errors=True)
        if os.path.exists(worker_config.BLENDER_VERSIONS_CACHE_FILE):
            os.remove(worker_config.BLENDER_VERSIONS_CACHE_FILE)
        print("Teardown complete.")


class TestRenderWorkflow(BaseE2ETest):
    """Groups tests for the standard render workflow."""

    def test_full_render_workflow(self):
        print("\n--- ACTION: Submitting render job ---")
        job_payload = {
            "name": "E2E CPU Render Test",
            "blend_file_path": worker_config.TEST_BLEND_FILE_PATH,
            "output_file_pattern": os.path.join(worker_config.TEST_OUTPUT_DIR, "e2e_render_####"),
            "start_frame": 1, "end_frame": 1, "blender_version": "4.5.0",
            "render_engine": "CYCLES",
            "render_device": "CPU",
        }
        create_response = requests.post(f"{MANAGER_URL}/jobs/", json=job_payload)
        assert create_response.status_code == 201
        job_id = create_response.json()['id']

        print("Polling API for DONE status...")
        final_status = ""
        for i in range(120):
            check_response = requests.get(f"{MANAGER_URL}/jobs/{job_id}/")
            if check_response.status_code == 200:
                current_status = check_response.json()['status']
                if current_status in ["DONE", "ERROR"]:
                    final_status = current_status
                    break
            time.sleep(2)
        assert final_status == "DONE"


class TestGpuWorkflow(BaseE2ETest):
    """Groups tests for the GPU selection workflow."""

    def test_gpu_job_command_generation(self):
        """
        Tests that for a GPU job, the command correctly includes --factory-startup
        and any other necessary GPU flags.
        """
        print("\n--- ACTION: Submitting GPU job with invalid file ---")
        job_payload = {
            "name": "E2E GPU Command Test",
            "blend_file_path": "/path/to/non_existent_file.blend",
            "output_file_pattern": os.path.join(worker_config.TEST_OUTPUT_DIR, "gpu_test_####"),
            "start_frame": 1, "end_frame": 1, "blender_version": "4.5.0",
            "render_engine": "CYCLES",
            "render_device": "GPU"
        }
        create_response = requests.post(f"{MANAGER_URL}/jobs/", json=job_payload)
        assert create_response.status_code == 201
        job_id = create_response.json()['id']

        print("Waiting for worker to prepare the render command...")
        command_logged = False
        start_time = time.time()
        # Drain the queue to look for the command
        while time.time() - start_time < 60:
            try:
                line = self.worker_log_queue.get(timeout=1)
                if "Running Command:" in line:
                    # Verify the NEW key behavior: --factory-startup is ALWAYS PRESENT.
                    assert "--factory-startup" in line
                    command_logged = True
                    break
            except queue.Empty:
                continue
        assert command_logged

        print("Polling API for ERROR status...")
        final_status = ""
        for i in range(30):
            check_response = requests.get(f"{MANAGER_URL}/jobs/{job_id}/")
            if check_response.status_code == 200:
                current_status = check_response.json()['status']
                if current_status == "ERROR":
                    final_status = current_status
                    break
            time.sleep(1)
        assert final_status == "ERROR"

    def test_full_gpu_render_workflow(self):
        """
        Tests a full end-to-end GPU render, but only runs if a GPU is available.
        """
        # --- MODIFIED & IMPROVED: Check for standard CI or GitHub-specific env var ---
        is_ci = os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true"
        is_macos_in_ci = platform.system() == "Darwin" and is_ci

        if not is_gpu_available() or is_macos_in_ci:
            pytest.skip("Skipping GPU test: No GPU available or running in macOS CI environment.")

        print("\n--- ACTION: Submitting full GPU render job ---")
        job_payload = {
            "name": "E2E Full GPU Render Test",
            "blend_file_path": worker_config.BENCHMARK_BLEND_FILE_PATH,
            "output_file_pattern": os.path.join(worker_config.TEST_OUTPUT_DIR, "e2e_gpu_render_####"),
            "start_frame": 1, "end_frame": 1, "blender_version": "4.5.0",
            "render_engine": "CYCLES",
            "render_device": "GPU",
        }
        create_response = requests.post(f"{MANAGER_URL}/jobs/", json=job_payload)
        assert create_response.status_code == 201
        job_id = create_response.json()['id']

        print("Polling API for DONE status...")
        final_status = ""
        for i in range(120):  # GPU renders can take longer
            check_response = requests.get(f"{MANAGER_URL}/jobs/{job_id}/")
            if check_response.status_code == 200:
                current_status = check_response.json()['status']
                print(f"  Attempt {i + 1}/120: Current job status is {current_status}")
                if current_status in ["DONE", "ERROR"]:
                    final_status = current_status
                    break
            time.sleep(2)
        assert final_status == "DONE", f"Job finished with status '{final_status}', expected 'DONE'."
        print("E2E Full GPU Render Test Passed!")


class TestWorkerRegistration(BaseE2ETest):
    """Tests the worker's registration and reporting capabilities."""

    def test_worker_reports_correct_gpu_devices(self):
        print("\n--- ACTION: Verifying worker hardware reporting ---")
        print("Detecting local GPU devices for comparison...")
        expected_gpus = system_monitor.detect_gpu_devices()
        print(f"Locally detected devices: {expected_gpus}")

        print("Querying API for worker's reported data...")
        response = requests.get(f"{MANAGER_URL}/heartbeat/")
        assert response.status_code == 200
        workers_data = response.json()
        assert len(workers_data) > 0

        local_hostname = socket.gethostname()
        worker_record = next((w for w in workers_data if w['hostname'] == local_hostname), None)
        assert worker_record is not None

        reported_tools = worker_record.get('available_tools', {})
        reported_gpus = reported_tools.get('gpu_devices', [])
        print(f"Worker reported devices: {reported_gpus}")

        assert sorted(reported_gpus) == sorted(expected_gpus)
        print("SUCCESS: Worker correctly reported its GPU capabilities.")