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
# tests/e2e/shared_setup.py

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

import requests

from sethlans_worker_agent import config as worker_config
from sethlans_worker_agent.tool_manager import tool_manager_instance
from sethlans_worker_agent.utils import blender_release_parser, file_operations

# --- Test Constants ---
MANAGER_URL = worker_config.MANAGER_API_URL.rstrip('/')
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TEST_DB_NAME = "test_e2e_db.sqlite3"
MOCK_TOOLS_DIR = Path(worker_config.MANAGED_TOOLS_DIR)
MEDIA_ROOT_FOR_TEST = Path(tempfile.mkdtemp())
E2E_BLENDER_SERIES = "4.5"


class BaseE2ETest:
    """
    A base class for end-to-end tests that handles the setup and teardown
    of the entire test environment, including a live manager, a live worker,
    and a clean database.
    """
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

    # FIX: Add the test blend file path as a class attribute
    test_blend_file = worker_config.TEST_BLEND_FILE_PATH

    @classmethod
    def _upload_test_asset(cls, name, file_path, project_id):
        """
        Helper function to upload a .blend file asset to the manager.

        This function handles the multipart/form-data request required for
        uploading files alongside text data fields.

        Args:
            name (str): The name to assign to the new asset.
            file_path (str): The local path to the .blend file.
            project_id (str): The UUID of the project this asset belongs to.

        Returns:
            int: The ID of the newly created asset.
        """
        with open(file_path, 'rb') as f:
            payload = {
                "name": name,
                "project": str(project_id),
            }
            files = {
                "blend_file": (os.path.basename(file_path), f, "application/octet-stream")
            }
            response = requests.post(f"{MANAGER_URL}/assets/", data=payload, files=files)

            if response.status_code == 400:
                print(f"ERROR: Asset upload failed with 400. Response: {response.text}")

            response.raise_for_status()
            return response.json()['id']

    @classmethod
    def _cache_blender_once(cls):
        """
        Dynamically finds the latest patch for the E2E_BLENDER_SERIES,
        then downloads and extracts it to a persistent system temp directory.
        This ensures Blender is downloaded only once per test session.
        """
        if cls._blender_version_for_test is None:
            print(f"\nDynamically determining latest patch for Blender {E2E_BLENDER_SERIES}.x series...")
            releases = blender_release_parser.get_blender_releases()
            latest_patch_for_series = None
            # The keys are sorted by version number descending, so the first match is the latest
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
    def start_worker(cls, log_queue, extra_env=None):
        """Starts the worker process and its log reader thread."""
        print("Starting Worker Agent...")
        test_env = os.environ.copy()
        test_env["SETHLANS_DB_NAME"] = TEST_DB_NAME
        test_env["DJANGO_SETTINGS_MODULE"] = "config.settings"
        test_env["SETHLANS_MEDIA_ROOT"] = str(MEDIA_ROOT_FOR_TEST)
        if extra_env:
            test_env.update(extra_env)

        # Apply CI-specific stability fixes for macOS virtualized environments
        is_ci = os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true"
        force_flag_is_set = "SETHLANS_FORCE_CPU_ONLY" in test_env or "SETHLANS_FORCE_GPU_ONLY" in test_env

        if platform.system() == "Darwin" and is_ci and cls.__name__ != 'TestWorkerRegistration' and not force_flag_is_set:
            print(f"\n[CI-FIX] macOS CI detected for {cls.__name__}. Forcing worker into CPU-only mode.")
            test_env["SETHLANS_FORCE_CPU_ONLY"] = "true"

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
    def setup_class(cls):
        """Set up the environment for all tests in this class."""
        print(f"\n--- SETUP: {cls.__name__} ---")

        # --- FIX: Generate a unique suffix for this test class run ---
        unique_suffix = int(time.time())

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

        print(f"Starting Django manager on port {worker_config.MANAGER_PORT}...")
        manager_command = [sys.executable, "manage.py", "runserver", str(worker_config.MANAGER_PORT), "--noreload"]
        cls.manager_process = subprocess.Popen(manager_command, cwd=PROJECT_ROOT, env=test_env,
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(5)

        print("Creating E2E test project...")
        project_payload = {"name": f"E2E-Test-Project-{unique_suffix}"}
        response = requests.post(f"{MANAGER_URL}/projects/", json=project_payload)
        response.raise_for_status()
        cls.project_id = response.json()['id']
        print(f"Test project created with ID: {cls.project_id}")

        print("Uploading test assets...")
        # --- FIX: Append unique suffix to asset names ---
        cls.scene_asset_id = cls._upload_test_asset(f"E2E Test Scene {unique_suffix}",
                                                    worker_config.TEST_BLEND_FILE_PATH,
                                                    cls.project_id)
        cls.bmw_asset_id = cls._upload_test_asset(f"E2E BMW Scene {unique_suffix}",
                                                  worker_config.BENCHMARK_BLEND_FILE_PATH,
                                                  cls.project_id)
        cls.anim_asset_id = cls._upload_test_asset(f"E2E Animation Scene {unique_suffix}",
                                                   worker_config.ANIMATION_BLEND_FILE_PATH,
                                                   cls.project_id)
        print(f"Assets uploaded: scene_id={cls.scene_asset_id}, bmw_id={cls.bmw_asset_id}, anim_id={cls.anim_asset_id}")

        cls.start_worker(cls.worker_log_queue)

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

        if "SETHLANS_MOCK_CPU_ONLY" in os.environ:
            del os.environ["SETHLANS_MOCK_CPU_ONLY"]

        print("Teardown complete.")