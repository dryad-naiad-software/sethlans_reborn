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
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
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
import tempfile
import queue
import threading
from pathlib import Path

import pytest
import requests

from sethlans_worker_agent import config as worker_config
from sethlans_worker_agent import system_monitor
from sethlans_worker_agent.tool_manager import tool_manager_instance
from sethlans_worker_agent.utils import blender_release_parser, file_operations
from workers.constants import RenderSettings


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

    # Class-level variable to store the path to the cached, extracted Blender directory
    _blender_cache_path = None

    @classmethod
    def _cache_blender_once(cls):
        """
        Downloads and extracts Blender to a persistent system temp directory.
        This method runs only once per test session, and subsequent calls will use the cache.
        """
        if cls._blender_cache_path and cls._blender_cache_path.exists():
            return  # Already cached in this test session run.

        cache_root = Path(tempfile.gettempdir()) / "sethlans_e2e_cache"
        cache_root.mkdir(exist_ok=True)

        version_req = "4.5.0"  # The version required by our tests
        platform_id = tool_manager_instance._get_platform_identifier()
        if not platform_id:
            raise RuntimeError("Could not determine platform identifier for E2E tests.")

        # This is the final path to the *extracted* Blender directory inside the cache.
        blender_install_dir_name = f"blender-{version_req}-{platform_id}"
        cls._blender_cache_path = cache_root / blender_install_dir_name

        if cls._blender_cache_path.exists():
            print(f"\nBlender {version_req} found in persistent cache: {cls._blender_cache_path}")
            return

        print(f"\nBlender {version_req} not found in cache. Downloading and extracting once...")

        releases = blender_release_parser.get_blender_releases()
        release_info = releases.get(version_req, {}).get(platform_id)
        if not release_info or not release_info.get('url') or not release_info.get('sha256'):
            raise RuntimeError(f"Cannot find download info for Blender {version_req} on {platform_id}")

        url = release_info['url']
        expected_hash = release_info['sha256']

        try:
            downloaded_archive = file_operations.download_file(url, str(cache_root))
            if not file_operations.verify_hash(downloaded_archive, expected_hash):
                raise IOError(f"Hash mismatch for cached Blender download: {downloaded_archive}")

            file_operations.extract_archive(downloaded_archive, str(cache_root))
            file_operations.cleanup_archive(downloaded_archive)
            print(f"Successfully cached Blender to {cls._blender_cache_path}")
        except Exception as e:
            # Clean up a failed attempt to ensure it retries next time.
            if cls._blender_cache_path.exists():
                shutil.rmtree(cls._blender_cache_path)
            cls._blender_cache_path = None  # Reset path so it retries
            raise RuntimeError(f"Failed to cache Blender for E2E tests: {e}")

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

        # Step 1: Download and extract Blender to a shared cache location if not already done.
        cls._cache_blender_once()

        # Step 2: Clean the test-specific directories for perfect isolation.
        if os.path.exists(TEST_DB_NAME):
            os.remove(TEST_DB_NAME)
        if MOCK_TOOLS_DIR.exists():
            shutil.rmtree(MOCK_TOOLS_DIR, ignore_errors=True)
        if Path(worker_config.TEST_OUTPUT_DIR).exists():
            shutil.rmtree(worker_config.TEST_OUTPUT_DIR, ignore_errors=True)
        if os.path.exists(worker_config.BLENDER_VERSIONS_CACHE_FILE):
            os.remove(worker_config.BLENDER_VERSIONS_CACHE_FILE)

        # Step 3: Copy the cached, extracted Blender into the test's sandboxed tool directory.
        blender_dir_for_test = MOCK_TOOLS_DIR / "blender"
        blender_dir_for_test.mkdir(parents=True)
        source_path = cls._blender_cache_path
        dest_path = blender_dir_for_test / source_path.name
        print(f"Copying cached Blender from {source_path} to {dest_path}")
        shutil.copytree(source_path, dest_path)

        # Step 4: Proceed with the original setup logic. The worker will now find the
        # pre-copied Blender and skip its own slow download and extraction steps.
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

        print("Starting Worker Agent...")
        worker_command = [sys.executable, "-m", "sethlans_worker_agent.agent", "--loglevel", "DEBUG"]
        cls.worker_process = subprocess.Popen(worker_command, cwd=PROJECT_ROOT, env=test_env, stdout=subprocess.PIPE,
                                              stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')

        cls.worker_log_thread = threading.Thread(target=cls._log_reader_thread, args=(cls.worker_process.stdout,))
        cls.worker_log_thread.daemon = True
        cls.worker_log_thread.start()

        worker_ready = False
        max_wait_seconds = 60  # Can be shorter now that download is skipped
        start_time = time.time()
        print("Waiting for worker to complete registration and initial setup...")
        while time.time() - start_time < max_wait_seconds:
            try:
                line = cls.worker_log_queue.get(timeout=1)
                print(f"  [SETUP LOG] {line.strip()}")
                if "Loop finished. Sleeping for" in line:
                    print("Worker is ready!")
                    worker_ready = True
                    break
            except queue.Empty:
                if cls.worker_process.poll() is not None:
                    # Drain queue to show any final error messages
                    while not cls.worker_log_queue.empty():
                        print(f"  [FINAL LOG] {cls.worker_log_queue.get_nowait().strip()}")
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
                print(f"  [WORKER] {line.strip()}")
            except queue.Empty:
                break
        print("--- END OF WORKER LOGS ---\n")

        if cls.worker_process:
            if platform.system() == "Windows":
                subprocess.run(f"taskkill /F /T /PID {cls.worker_process.pid}", check=False, capture_output=True, shell=True)
            else:
                cls.worker_process.kill()

        if cls.manager_process:
            if platform.system() == "Windows":
                subprocess.run(f"taskkill /F /T /PID {cls.manager_process.pid}", check=False, capture_output=True, shell=True)
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
            "render_settings": {
                RenderSettings.SAMPLES: 10,
                RenderSettings.RESOLUTION_PERCENTAGE: 10
            }
        }
        create_response = requests.post(f"{MANAGER_URL}/jobs/", json=job_payload)
        assert create_response.status_code == 201
        job_id = create_response.json()['id']
        job_url = f"{MANAGER_URL}/jobs/{job_id}/"

        print("Polling API for DONE status...")
        final_status = ""
        for i in range(120):
            check_response = requests.get(job_url)
            if check_response.status_code == 200:
                current_status = check_response.json()['status']
                if current_status in ["DONE", "ERROR"]:
                    final_status = current_status
                    break
            time.sleep(2)
        assert final_status == "DONE"

        print("Verifying render time was recorded...")
        final_job_data = requests.get(job_url).json()
        assert final_job_data.get('render_time_seconds') is not None
        assert final_job_data.get('render_time_seconds') > 0


class TestGpuWorkflow(BaseE2ETest):
    """Groups tests for the GPU selection workflow."""

    def test_gpu_job_omits_factory_startup(self):
        """
        Tests that for a GPU job, the '--factory-startup' flag is correctly omitted
        to allow the pre-configured .blend file's settings to be used.
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
        while time.time() - start_time < 60:
            try:
                line = self.worker_log_queue.get(timeout=1)
                if "Running Command:" in line:
                    assert "--factory-startup" not in line
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
        for i in range(120):
            check_response = requests.get(f"{MANAGER_URL}/jobs/{job_id}/")
            if check_response.status_code == 200:
                current_status = check_response.json()['status']
                print(f"  Attempt {i + 1}/120: Current job status is {current_status}")
                if current_status in ["DONE", "ERROR"]:
                    final_status = current_status
                    break
            time.sleep(2)
        assert final_status == "DONE", f"Job finished with status '{final_status}', expected 'DONE'."
        print("E2E Full GPU Render Test Passed!")


class TestAnimationWorkflow(BaseE2ETest):
    """Groups tests for the animation rendering workflow."""

    def test_animation_render_workflow(self):
        """
        Tests submitting a multi-frame animation, polling for completion,
        and verifying that all output files are created.
        """
        start_frame, end_frame = 1, 5
        total_frames = (end_frame - start_frame) + 1
        output_pattern = os.path.join(worker_config.TEST_OUTPUT_DIR, "anim_render_####")

        print("\n--- ACTION: Submitting animation job ---")
        anim_payload = {
            "name": "E2E Animation Test",
            "blend_file_path": worker_config.ANIMATION_BLEND_FILE_PATH,
            "output_file_pattern": output_pattern,
            "start_frame": start_frame,
            "end_frame": end_frame,
            "blender_version": "4.5.0",
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
            print(f"  Attempt {i + 1}/150: {data.get('progress', 'N/A')}")
            if completed_frames == total_frames:
                completed = True
                break
            time.sleep(2)

        assert completed, f"Animation did not complete in time. Only {completed_frames}/{total_frames} frames finished."

        print("Verifying output files...")
        for frame_num in range(start_frame, end_frame + 1):
            expected_file_path = output_pattern.replace("####", f"{frame_num:04d}") + ".png"
            assert os.path.exists(expected_file_path), f"Output file is missing: {expected_file_path}"

        print(f"SUCCESS: All {total_frames} animation frames were rendered successfully.")

        print("Verifying total animation render time was recorded...")
        final_anim_data = requests.get(anim_url).json()
        assert final_anim_data.get('total_render_time_seconds') is not None
        assert final_anim_data.get('total_render_time_seconds') > 0


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