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
# tests/e2e/test_full_workflow.py

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
import io
from pathlib import Path

import pytest
import requests
from PIL import Image
from unittest.mock import patch

from sethlans_worker_agent import config as worker_config
from sethlans_worker_agent import system_monitor
from sethlans_worker_agent.tool_manager import tool_manager_instance
from sethlans_worker_agent.utils import blender_release_parser, file_operations
from workers.constants import RenderSettings, TilingConfiguration, RenderDevice


def is_gpu_available():
    """Checks if a compatible GPU is available for rendering."""
    # Reset cache for this check to be accurate for the host machine
    system_monitor._gpu_devices_cache = None
    devices = system_monitor.detect_gpu_devices()
    # Ensure cache is clear for subsequent test runs
    system_monitor._gpu_devices_cache = None
    return len(devices) > 0


# --- Test Constants ---
MANAGER_URL = "http://127.0.0.1:8000/api"
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TEST_DB_NAME = "test_e2e_db.sqlite3"
MOCK_TOOLS_DIR = Path(worker_config.MANAGED_TOOLS_DIR)
MEDIA_ROOT_FOR_TEST = Path(tempfile.mkdtemp())
E2E_BLENDER_SERIES = "4.5"


class BaseE2ETest:
    manager_process = None
    worker_process = None
    worker_log_thread = None
    worker_log_queue = queue.Queue()
    _blender_cache_path = None
    _blender_version_for_test = None

    project_id = None
    scene_asset_id = None
    bmw_asset_id = None
    anim_asset_id = None

    @classmethod
    def _upload_test_asset(cls, name, file_path, project_id):
        """Helper to upload a blend file and return its asset ID."""
        with open(file_path, 'rb') as f:
            payload = {
                "name": name,
                "project": project_id,
            }
            files = {
                "blend_file": (os.path.basename(file_path), f.read(), "application/octet-stream")
            }
            response = requests.post(f"{MANAGER_URL}/assets/", data=payload, files=files)
            response.raise_for_status()
            return response.json()['id']

    @classmethod
    def _cache_blender_once(cls):
        """
        Dynamically finds the latest patch for the E2E_BLENDER_SERIES,
        then downloads and extracts it to a persistent system temp directory.
        """
        if cls._blender_version_for_test is None:
            print(f"\nDynamically determining latest patch for Blender {E2E_BLENDER_SERIES}.x series...")
            releases = blender_release_parser.get_blender_releases()
            latest_patch_for_series = None
            for version in releases.keys():
                if version.startswith(E2E_BLENDER_SERIES + '.'):
                    latest_patch_for_series = version
                    break

            if not latest_patch_for_series:
                raise RuntimeError(f"Cannot find any patch release for Blender series {E2E_BLENDER_SERIES}")

            cls._blender_version_for_test = latest_patch_for_series
            print(f"Found latest patch: {cls._blender_version_for_test}")

        version_req = cls._blender_version_for_test

        if cls._blender_cache_path and cls._blender_cache_path.exists():
            return

        cache_root = Path(tempfile.gettempdir()) / "sethlans_e2e_cache"
        cache_root.mkdir(exist_ok=True)

        platform_id = tool_manager_instance._get_platform_identifier()
        if not platform_id:
            raise RuntimeError("Could not determine platform identifier for E2E tests.")

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
            if cls._blender_cache_path.exists():
                shutil.rmtree(cls._blender_cache_path)
            cls._blender_cache_path = None
            raise RuntimeError(f"Failed to cache Blender for E2E tests: {e}")

    @classmethod
    def _log_reader_thread(cls, pipe, log_queue):
        """Reads lines from a pipe and puts them in the specified queue."""
        try:
            for line in iter(pipe.readline, ''):
                log_queue.put(line)
        finally:
            pipe.close()

    @classmethod
    def setup_class(cls):
        """Set up the environment for all tests in this class."""
        print(f"\n--- SETUP: {cls.__name__} ---")

        cls._cache_blender_once()
        if os.path.exists(TEST_DB_NAME): os.remove(TEST_DB_NAME)
        if MOCK_TOOLS_DIR.exists(): shutil.rmtree(MOCK_TOOLS_DIR, ignore_errors=True)
        if Path(worker_config.MANAGED_ASSETS_DIR).exists(): shutil.rmtree(worker_config.MANAGED_ASSETS_DIR,
                                                                          ignore_errors=True)
        if Path(worker_config.WORKER_OUTPUT_DIR).exists(): shutil.rmtree(worker_config.WORKER_OUTPUT_DIR,
                                                                         ignore_errors=True)
        if os.path.exists(worker_config.BLENDER_VERSIONS_CACHE_FILE): os.remove(
            worker_config.BLENDER_VERSIONS_CACHE_FILE)
        if MEDIA_ROOT_FOR_TEST.exists(): shutil.rmtree(MEDIA_ROOT_FOR_TEST, ignore_errors=True)
        MEDIA_ROOT_FOR_TEST.mkdir()

        blender_dir_for_test = MOCK_TOOLS_DIR / "blender"
        blender_dir_for_test.mkdir(parents=True)
        source_path = cls._blender_cache_path
        dest_path = blender_dir_for_test / source_path.name
        print(f"Copying cached Blender from {source_path} to {dest_path}")
        shutil.copytree(source_path, dest_path)

        print("Running migrations...")
        test_env = os.environ.copy()
        test_env["SETHLANS_DB_NAME"] = TEST_DB_NAME
        test_env["DJANGO_SETTINGS_MODULE"] = "config.settings"
        test_env["SETHLANS_MEDIA_ROOT"] = str(MEDIA_ROOT_FOR_TEST)
        subprocess.run([sys.executable, "manage.py", "migrate"], cwd=PROJECT_ROOT, env=test_env, check=True,
                       capture_output=True)

        print("Starting Django manager...")
        manager_command = [sys.executable, "manage.py", "runserver", "--noreload"]
        cls.manager_process = subprocess.Popen(manager_command, cwd=PROJECT_ROOT, env=test_env,
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(5)

        print("Creating E2E test project...")
        project_payload = {"name": f"E2E-Test-Project-{int(time.time())}"}
        response = requests.post(f"{MANAGER_URL}/projects/", json=project_payload)
        response.raise_for_status()
        cls.project_id = response.json()['id']
        print(f"Test project created with ID: {cls.project_id}")

        print("Uploading test assets...")
        cls.scene_asset_id = cls._upload_test_asset("E2E Test Scene", worker_config.TEST_BLEND_FILE_PATH,
                                                    cls.project_id)
        cls.bmw_asset_id = cls._upload_test_asset("E2E BMW Scene", worker_config.BENCHMARK_BLEND_FILE_PATH,
                                                  cls.project_id)
        cls.anim_asset_id = cls._upload_test_asset("E2E Animation Scene", worker_config.ANIMATION_BLEND_FILE_PATH,
                                                   cls.project_id)
        print(f"Assets uploaded: scene_id={cls.scene_asset_id}, bmw_id={cls.bmw_asset_id}, anim_id={cls.anim_asset_id}")

        # Start worker as part of the main setup
        cls.start_worker(cls.worker_log_queue)

    @classmethod
    def start_worker(cls, log_queue):
        """Starts the worker process and its log reader thread."""
        print("Starting Worker Agent...")
        test_env = os.environ.copy()
        test_env["SETHLANS_DB_NAME"] = TEST_DB_NAME
        test_env["DJANGO_SETTINGS_MODULE"] = "config.settings"
        test_env["SETHLANS_MEDIA_ROOT"] = str(MEDIA_ROOT_FOR_TEST)
        worker_command = [sys.executable, "-m", "sethlans_worker_agent.agent", "--loglevel", "DEBUG"]
        cls.worker_process = subprocess.Popen(worker_command, cwd=PROJECT_ROOT, env=test_env, stdout=subprocess.PIPE,
                                              stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')

        cls.worker_log_thread = threading.Thread(target=cls._log_reader_thread,
                                                 args=(cls.worker_process.stdout, log_queue))
        cls.worker_log_thread.daemon = True
        cls.worker_log_thread.start()

        worker_ready = False
        max_wait_seconds = 180
        start_time = time.time()
        print("Waiting for worker to complete registration and initial setup...")
        while time.time() - start_time < max_wait_seconds:
            try:
                line = log_queue.get(timeout=1)
                print(f"  [SETUP LOG] {line.strip()}")
                if "Loop finished. Sleeping for" in line:
                    print("Worker is ready!")
                    worker_ready = True
                    break
            except queue.Empty:
                if cls.worker_process.poll() is not None:
                    while not log_queue.empty():
                        print(f"  [FINAL LOG] {log_queue.get_nowait().strip()}")
                    raise RuntimeError("Worker process terminated unexpectedly during setup.")
                continue

        if not worker_ready:
            raise RuntimeError("Worker agent did not become ready within the time limit.")

    @classmethod
    def teardown_class(cls):
        print(f"\n--- TEARDOWN: {cls.__name__} ---")
        print("\n--- CAPTURED WORKER LOGS ---")
        while not cls.worker_log_queue.empty():
            try:
                line = cls.worker_log_queue.get_nowait()
                print(f"  [WORKER] {line.strip()}")
            except queue.Empty:
                break
        print("--- END OF WORKER LOGS ---\n")

        if cls.worker_process and cls.worker_process.poll() is None:
            if platform.system() == "Windows":
                subprocess.run(f"taskkill /F /T /PID {cls.worker_process.pid}", check=False, capture_output=True,
                               shell=True)
            else:
                cls.worker_process.kill()

        if cls.manager_process and cls.manager_process.poll() is None:
            if platform.system() == "Windows":
                subprocess.run(f"taskkill /F /T /PID {cls.manager_process.pid}", check=False, capture_output=True,
                               shell=True)
            else:
                cls.manager_process.kill()

        if cls.worker_log_thread and cls.worker_log_thread.is_alive():
            cls.worker_log_thread.join(timeout=5)

        if os.path.exists(TEST_DB_NAME): os.remove(TEST_DB_NAME)
        if Path(worker_config.WORKER_OUTPUT_DIR).exists(): shutil.rmtree(worker_config.WORKER_OUTPUT_DIR,
                                                                         ignore_errors=True)
        if MOCK_TOOLS_DIR.exists(): shutil.rmtree(MOCK_TOOLS_DIR, ignore_errors=True)
        if Path(worker_config.MANAGED_ASSETS_DIR).exists(): shutil.rmtree(worker_config.MANAGED_ASSETS_DIR,
                                                                          ignore_errors=True)
        if os.path.exists(worker_config.BLENDER_VERSIONS_CACHE_FILE): os.remove(
            worker_config.BLENDER_VERSIONS_CACHE_FILE)
        if MEDIA_ROOT_FOR_TEST.exists(): shutil.rmtree(MEDIA_ROOT_FOR_TEST, ignore_errors=True)

        print("Teardown complete.")


class TestRenderWorkflow(BaseE2ETest):
    def test_full_render_workflow(self):
        print("\n--- ACTION: Submitting render job ---")
        job_payload = {
            "name": "E2E CPU Render Test",
            "asset_id": self.scene_asset_id,
            "output_file_pattern": "e2e_render_####",
            "start_frame": 1, "end_frame": 1, "blender_version": self._blender_version_for_test,
            "render_engine": "CYCLES",
            "render_device": "CPU",  # Explicitly test CPU
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

        print("Verifying final job data and output file...")
        final_job_response = requests.get(job_url)
        assert final_job_response.status_code == 200
        final_job_data = final_job_response.json()

        assert final_job_data.get('render_time_seconds') is not None
        assert final_job_data.get('render_time_seconds') > 0
        assert 'output_file' in final_job_data
        output_url = final_job_data['output_file']
        assert output_url is not None

        print(f"Downloading output file from {output_url}...")
        download_response = requests.get(output_url)
        assert download_response.status_code == 200
        assert len(download_response.content) > 0, "Downloaded output file is empty."


class TestGpuWorkflow(BaseE2ETest):
    def test_full_gpu_render_workflow(self):
        is_ci = os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true"
        is_macos_in_ci = platform.system() == "Darwin" and is_ci

        if not is_gpu_available() or is_macos_in_ci:
            pytest.skip("Skipping GPU test: No GPU available or running in macOS CI environment.")

        print("\n--- ACTION: Submitting full GPU render job ---")
        job_payload = {
            "name": "E2E Full GPU Render Test",
            "asset_id": self.bmw_asset_id,
            "output_file_pattern": "e2e_gpu_render_####",
            "start_frame": 1, "end_frame": 1, "blender_version": self._blender_version_for_test,
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
                print(f"  Attempt {i + 1}/120: Current job status is {current_status}")
                if current_status in ["DONE", "ERROR"]:
                    final_status = current_status
                    break
            time.sleep(2)
        assert final_status == "DONE", f"Job finished with status '{final_status}', expected 'DONE'."
        print("E2E Full GPU Render Test Passed!")


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

        print("Verifying all child jobs have an output file URL...")
        jobs_response = requests.get(f"{MANAGER_URL}/jobs/?animation={anim_id}")
        assert jobs_response.status_code == 200
        child_jobs = jobs_response.json()
        assert len(child_jobs) == total_frames

        for job in child_jobs:
            assert job.get('output_file') is not None, f"Job {job['id']} is missing its output file URL."

        print("SUCCESS: All animation frames were rendered and uploaded successfully.")

        print("Verifying total animation render time was recorded...")
        final_anim_data = requests.get(anim_url).json()
        assert final_anim_data.get('total_render_time_seconds') is not None
        assert final_anim_data.get('total_render_time_seconds') > 0

    def test_animation_with_frame_step(self):
        """
        Tests that an animation with a frame_step > 1 spawns the correct number of jobs.
        """
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

        # Verify the correct jobs were created and completed
        jobs_response = requests.get(f"{MANAGER_URL}/jobs/?animation={anim_id}")
        assert jobs_response.status_code == 200
        child_jobs = jobs_response.json()
        assert len(child_jobs) == expected_job_count

        spawned_frames = sorted([job['start_frame'] for job in child_jobs])
        assert spawned_frames == expected_frames
        print(f"SUCCESS: Correctly spawned jobs for frames {spawned_frames}.")


class TestTiledWorkflow(BaseE2ETest):
    def test_tiled_render_workflow(self):
        is_ci = os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true"
        is_macos_in_ci = platform.system() == "Darwin" and is_ci

        if is_macos_in_ci:
            pytest.skip("Skipping tiled GPU-implicit test on macOS CI to maintain stability.")

        print("\n--- ACTION: Submitting tiled render job ---")
        tiled_job_payload = {
            "name": "E2E Tiled Render Test",
            "project": self.project_id,
            "asset_id": self.bmw_asset_id,
            "final_resolution_x": 400,
            "final_resolution_y": 400,
            "tile_count_x": 2,
            "tile_count_y": 2,
            "blender_version": "4.5",
            "render_settings": {RenderSettings.SAMPLES: 32}
        }
        create_response = requests.post(f"{MANAGER_URL}/tiled-jobs/", json=tiled_job_payload)
        assert create_response.status_code == 201
        tiled_job_id = create_response.json()['id']
        tiled_job_url = f"{MANAGER_URL}/tiled-jobs/{tiled_job_id}/"

        print("Polling API for DONE status...")
        final_status = ""
        for i in range(180):
            check_response = requests.get(tiled_job_url)
            assert check_response.status_code == 200
            data = check_response.json()
            current_status = data['status']
            print(f"  Attempt {i + 1}/180: Current job status is {current_status} ({data.get('progress', 'N/A')})")
            if current_status in ["DONE", "ERROR"]:
                final_status = current_status
                break
            time.sleep(2)
        assert final_status == "DONE"

        print("Verifying final job data and output file...")
        final_job_response = requests.get(tiled_job_url)
        assert final_job_response.status_code == 200
        final_job_data = final_job_response.json()

        assert final_job_data.get('total_render_time_seconds') > 0
        output_url = final_job_data.get('output_file')
        assert output_url is not None

        print(f"Downloading final assembled image from {output_url}...")
        download_response = requests.get(output_url)
        assert download_response.status_code == 200

        image_data = io.BytesIO(download_response.content)
        with Image.open(image_data) as img:
            assert img.size == (400, 400)


class TestWorkerRegistration(BaseE2ETest):
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


class TestTiledAnimationWorkflow(BaseE2ETest):
    def test_tiled_animation_workflow(self):
        """
        Tests the full workflow for a tiled animation: submission, rendering,
        frame-by-frame assembly, and final verification.
        """
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

        assert final_anim_data['total_render_time_seconds'] > 0
        assert len(final_anim_data['frames']) == total_frames

        for frame_data in final_anim_data['frames']:
            assert frame_data['status'] == 'DONE'
            frame_url = frame_data['output_file']
            assert frame_url is not None
            print(f"Downloading and verifying assembled frame {frame_data['frame_number']} from {frame_url}...")
            download_response = requests.get(frame_url)
            assert download_response.status_code == 200
            image_data = io.BytesIO(download_response.content)
            with Image.open(image_data) as img:
                assert img.size == (200, 200)

        print("SUCCESS: Tiled animation workflow completed successfully.")


class TestJobFiltering(BaseE2ETest):
    """
    This test has a custom setup to simulate a CPU-only worker.
    """

    @classmethod
    def setup_class(cls):
        """Override setup to mock before starting the worker."""
        print("\n--- Applying mock for CPU-only worker BEFORE setup ---")
        os.environ["SETHLANS_MOCK_CPU_ONLY"] = "true"
        # Now call the original setup, which will start a worker with the env var
        super().setup_class()

    @classmethod
    def teardown_class(cls):
        """Ensure the environment variable is cleaned up."""
        super().teardown_class()
        print("\n--- Stopping CPU-only worker mock ---")
        if "SETHLANS_MOCK_CPU_ONLY" in os.environ:
            del os.environ["SETHLANS_MOCK_CPU_ONLY"]

    def test_cpu_worker_ignores_gpu_job(self):
        print("\n--- ACTION: Testing that a CPU-only worker ignores GPU-only jobs ---")

        # 1. Submit one GPU job and one CPU job
        print("Submitting GPU-only and CPU-only jobs...")
        gpu_job_payload = {
            "name": "GPU-Only Job",
            "asset_id": self.scene_asset_id,
            "render_device": RenderDevice.GPU,
            "output_file_pattern": "gpu_filter_test_####",
            "blender_version": self._blender_version_for_test,
            "render_settings": {RenderSettings.SAMPLES: 8}  # Add settings to match working tests
        }
        cpu_job_payload = {
            "name": "CPU-Only Job",
            "asset_id": self.scene_asset_id,
            "render_device": RenderDevice.CPU,
            "output_file_pattern": "cpu_filter_test_####",
            "blender_version": self._blender_version_for_test,
            "render_settings": {RenderSettings.SAMPLES: 8}  # Add settings to match working tests
        }
        gpu_response = requests.post(f"{MANAGER_URL}/jobs/", json=gpu_job_payload)
        cpu_response = requests.post(f"{MANAGER_URL}/jobs/", json=cpu_job_payload)
        assert gpu_response.status_code == 201
        assert cpu_response.status_code == 201
        gpu_job_id = gpu_response.json()['id']
        cpu_job_id = cpu_response.json()['id']

        # 2. Poll and verify
        print("Polling to verify correct job was taken...")
        cpu_job_completed = False
        for _ in range(30):  # Wait up to 60 seconds
            cpu_job_status_response = requests.get(f"{MANAGER_URL}/jobs/{cpu_job_id}/")
            gpu_job_status_response = requests.get(f"{MANAGER_URL}/jobs/{gpu_job_id}/")
            assert cpu_job_status_response.status_code == 200
            assert gpu_job_status_response.status_code == 200

            cpu_job_status = cpu_job_status_response.json()['status']
            gpu_job_status = gpu_job_status_response.json()['status']

            # GPU job should never be touched by our mocked CPU-only worker
            assert gpu_job_status == "QUEUED"

            if cpu_job_status == "DONE":
                cpu_job_completed = True
                break
            time.sleep(2)

        assert cpu_job_completed, "CPU job was not completed by the worker."
        print("SUCCESS: CPU-only worker correctly ignored the GPU job and processed the CPU job.")


class TestProjectPauseWorkflow(BaseE2ETest):
    """
    Tests the end-to-end workflow of pausing a project and ensuring
    workers do not pick up jobs from it until it is unpaused.
    """

    def test_worker_respects_paused_project(self):
        print("\n--- ACTION: Testing worker respects paused projects ---")

        # 1. Create a second project that will be paused
        paused_project_payload = {"name": f"E2E-Paused-Project-{int(time.time())}"}
        response = requests.post(f"{MANAGER_URL}/projects/", json=paused_project_payload)
        assert response.status_code == 201
        paused_project_id = response.json()['id']
        paused_asset_id = self._upload_test_asset("Paused Project Asset", worker_config.TEST_BLEND_FILE_PATH,
                                                  paused_project_id)

        # 2. Create one job in the active project and one in the soon-to-be-paused project
        active_job_payload = {
            "name": "Active Project Job", "asset_id": self.scene_asset_id,
            "output_file_pattern": "active_####", "render_settings": {RenderSettings.SAMPLES: 1}
        }
        paused_job_payload = {
            "name": "Paused Project Job", "asset_id": paused_asset_id,
            "output_file_pattern": "paused_####", "render_settings": {RenderSettings.SAMPLES: 1}
        }

        active_job_res = requests.post(f"{MANAGER_URL}/jobs/", json=active_job_payload)
        paused_job_res = requests.post(f"{MANAGER_URL}/jobs/", json=paused_job_payload)
        assert active_job_res.status_code == 201
        assert paused_job_res.status_code == 201
        active_job_id = active_job_res.json()['id']
        paused_job_id = paused_job_res.json()['id']

        # 3. Pause the second project
        print(f"Pausing project {paused_project_id}...")
        pause_res = requests.post(f"{MANAGER_URL}/projects/{paused_project_id}/pause/")
        assert pause_res.status_code == 200
        assert pause_res.json()['is_paused'] is True

        # 4. Wait and verify that only the active job is completed
        print("Waiting for worker to process active job...")
        active_job_done = False
        for _ in range(15):  # 30 second timeout
            active_job_status = requests.get(f"{MANAGER_URL}/jobs/{active_job_id}/").json()['status']
            paused_job_status = requests.get(f"{MANAGER_URL}/jobs/{paused_job_id}/").json()['status']

            assert paused_job_status == "QUEUED", "Paused job was processed prematurely!"

            if active_job_status == "DONE":
                active_job_done = True
                break
            time.sleep(2)

        assert active_job_done, "Active project job did not complete in time."
        print("SUCCESS: Worker completed the active job and ignored the paused one.")

        # 5. Unpause the project and verify the second job is now completed
        print(f"Unpausing project {paused_project_id}...")
        unpause_res = requests.post(f"{MANAGER_URL}/projects/{paused_project_id}/unpause/")
        assert unpause_res.status_code == 200
        assert unpause_res.json()['is_paused'] is False

        print("Waiting for worker to process the now-unpaused job...")
        paused_job_done = False
        for _ in range(15):  # 30 second timeout
            paused_job_status = requests.get(f"{MANAGER_URL}/jobs/{paused_job_id}/").json()['status']
            if paused_job_status == "DONE":
                paused_job_done = True
                break
            time.sleep(2)

        assert paused_job_done, "Paused project job did not complete after being unpaused."
        print("SUCCESS: Worker completed the job after the project was unpaused.")