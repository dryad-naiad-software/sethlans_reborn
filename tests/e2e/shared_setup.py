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
"""
Provides the base class and setup/teardown logic for the E2E test suite.

This module is responsible for orchestrating the entire test environment, which
includes:
- Starting and stopping the Django manager process.
- Starting and stopping a live worker agent subprocess.
- Creating and destroying a temporary, isolated test database.
- Managing temporary directories for media files and worker assets.
- Caching the Blender download to avoid repeated downloads during test runs.
- Pre-uploading all necessary .blend file assets for tests to use.
"""

import os
import shutil
import subprocess
import sys
import time
import platform
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
# Define the test database path as an absolute path to avoid CWD issues
TEST_DB_NAME = PROJECT_ROOT / "test_e2e_db.sqlite3"
MOCK_TOOLS_DIR = Path(worker_config.MANAGED_TOOLS_DIR)
# Use a predictable, local directory for test artifacts
ARTIFACTS_ROOT_FOR_TEST = PROJECT_ROOT / "test_artifacts"
MEDIA_ROOT_FOR_TEST = ARTIFACTS_ROOT_FOR_TEST / "media"
E2E_BLENDER_SERIES = "4.5"


class BaseE2ETest:
    """
    A base class for end-to-end tests that handles the setup and teardown
    of the entire test environment, including a live manager, a live worker,
    and a clean database.

    Class-level attributes are used to store state that is shared across all
    tests within a single test class, such as the manager/worker processes
    and the IDs of pre-uploaded assets.
    """
    # Process management
    manager_process = None
    worker_process = None

    # Worker log capturing
    worker_log_thread = None
    worker_log_queue = queue.Queue()

    # Blender version caching
    _blender_cache_path = None
    _blender_version_for_test = None

    # Shared entity IDs
    project_id = None
    short_project_id = None
    scene_asset_id = None
    bmw_asset_id = None
    anim_asset_id = None

    @classmethod
    def _upload_test_asset(cls, name: str, file_path: Path, project_id: str) -> int:
        """
        Helper function to upload a .blend file asset to the manager.

        Args:
            name (str): The name to assign to the new asset.
            file_path (Path): The local path (as a Path object) to the .blend file.
            project_id (str): The UUID of the project this asset belongs to.

        Returns:
            int: The ID of the newly created asset.
        """
        # Explicitly cast Path to string for open() to be maximally robust.
        with open(str(file_path), 'rb') as f:
            payload = {"name": name, "project": str(project_id)}
            files = {"blend_file": (os.path.basename(file_path), f, "application/octet-stream")}
            response = requests.post(f"{MANAGER_URL}/assets/", data=payload, files=files)

            # Add improved logging to show the server's validation error on failure.
            if response.status_code != 201:
                print(f"ERROR: Asset upload for '{name}' failed with status {response.status_code}.")
                print(f"Response body: {response.text}")

            response.raise_for_status()
            return response.json()['id']

    @classmethod
    def _cache_blender_once(cls):
        """
        Dynamically finds the latest patch for E2E_BLENDER_SERIES, then
        downloads and extracts it to a persistent system temp directory. This
        ensures Blender is downloaded only once per full test session.
        """
        if cls._blender_version_for_test is None:
            print(f"\nDynamically determining latest patch for Blender {E2E_BLENDER_SERIES}.x series...")
            releases = blender_release_parser.get_blender_releases()
            latest_patch_for_series = next((v for v in releases if v.startswith(E2E_BLENDER_SERIES + '.')), None)

            if not latest_patch_for_series:
                raise RuntimeError(f"Cannot find any patch release for Blender series {E2E_BLENDER_SERIES}")

            cls._blender_version_for_test = latest_patch_for_series
            print(f"Found latest patch: {cls._blender_version_for_test}")

        cache_root = Path(tempfile.gettempdir()) / "sethlans_e2e_cache"
        cache_root.mkdir(exist_ok=True)
        platform_id = tool_manager_instance._get_platform_identifier()
        blender_install_dir_name = f"blender-{cls._blender_version_for_test}-{platform_id}"
        cls._blender_cache_path = cache_root / blender_install_dir_name

        if cls._blender_cache_path.exists():
            print(f"\nBlender {cls._blender_version_for_test} found in persistent cache: {cls._blender_cache_path}")
            return

        print(f"\nBlender {cls._blender_version_for_test} not found in cache. Downloading and extracting once...")
        releases = blender_release_parser.get_blender_releases()
        release_info = releases.get(cls._blender_version_for_test, {}).get(platform_id)
        if not release_info or not all(k in release_info for k in ['url', 'sha256']):
            raise RuntimeError(
                f"Cannot find download info for Blender {cls._blender_version_for_test} on {platform_id}")

        try:
            downloaded_archive = file_operations.download_file(release_info['url'], str(cache_root))
            if not file_operations.verify_hash(downloaded_archive, release_info['sha256']):
                raise IOError(f"Hash mismatch for cached Blender download: {downloaded_archive}")

            file_operations.extract_archive(downloaded_archive, str(cache_root))
            file_operations.cleanup_archive(downloaded_archive)
            print(f"Successfully cached Blender to {cls._blender_cache_path}")
        except Exception as e:
            if cls._blender_cache_path.exists(): shutil.rmtree(cls._blender_cache_path)
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
        """
        Starts the worker process and its log reader thread.

        Applies a stability fix for macOS CI runners by forcing the worker
        into CPU-only mode for most tests. This is disabled for test suites
        that specifically need to validate hardware detection.
        """
        print("Starting Worker Agent...")
        test_env = os.environ.copy()
        test_env.update({
            "SETHLANS_DB_NAME": str(TEST_DB_NAME),  # Pass absolute path as string
            "DJANGO_SETTINGS_MODULE": "config.settings",
            "SETHLANS_MEDIA_ROOT": str(MEDIA_ROOT_FOR_TEST)
        })
        if extra_env:
            test_env.update(extra_env)

        is_ci = os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true"
        force_flag_is_set = "SETHLANS_FORCE_CPU_ONLY" in test_env or "SETHLANS_FORCE_GPU_ONLY" in test_env

        # FIX: The hardware reporting test is in `TestWorkerBehavior`, not the non-existent
        # `TestWorkerRegistration`. This fix ensures the stability flag is NOT applied to
        # the test that validates hardware detection.
        if platform.system() == "Darwin" and is_ci and cls.__name__ != 'TestWorkerBehavior' and not force_flag_is_set:
            print(f"\n[CI-FIX] macOS CI detected for {cls.__name__}. Forcing worker into CPU-only mode.")
            test_env["SETHLANS_FORCE_CPU_ONLY"] = "true"

        worker_command = [sys.executable, "-m", "sethlans_worker_agent.agent", "--loglevel", "DEBUG"]
        cls.worker_process = subprocess.Popen(
            worker_command, cwd=PROJECT_ROOT, env=test_env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace'
        )
        cls.worker_log_thread = threading.Thread(target=cls._log_reader_thread,
                                                 args=(cls.worker_process.stdout, log_queue))
        cls.worker_log_thread.daemon = True
        cls.worker_log_thread.start()

        print("Waiting for worker to complete registration and initial setup...")
        start_time = time.time()
        while time.time() - start_time < 180:
            try:
                line = log_queue.get(timeout=1)
                print(f"  [SETUP LOG] {line.strip()}")
                if "Loop finished. Sleeping for" in line:
                    print("Worker is ready!")
                    return
            except queue.Empty:
                if cls.worker_process.poll() is not None:
                    raise RuntimeError("Worker process terminated unexpectedly during setup.")
        raise RuntimeError("Worker agent did not become ready within the time limit.")

    @classmethod
    def setup_class(cls):
        """Set up the environment once for all tests in this class."""
        print(f"\n--- SETUP: {cls.__name__} ---")
        # Clean up previous run artifacts that are not handled by session setup
        if os.path.exists(TEST_DB_NAME): os.remove(TEST_DB_NAME)
        if MOCK_TOOLS_DIR.exists():
            shutil.rmtree(MOCK_TOOLS_DIR, ignore_errors=True)
        if os.path.exists(worker_config.BLENDER_VERSIONS_CACHE_FILE):
            os.remove(worker_config.BLENDER_VERSIONS_CACHE_FILE)

        # The session-level hooks now handle artifact directory cleanup.
        # We just need to ensure the media root exists for the current test class.
        MEDIA_ROOT_FOR_TEST.mkdir(parents=True, exist_ok=True)

        # Prepare Blender installation for the worker
        cls._cache_blender_once()
        blender_dir_for_test = MOCK_TOOLS_DIR / "blender"
        blender_dir_for_test.mkdir(parents=True, exist_ok=True)
        shutil.copytree(cls._blender_cache_path, blender_dir_for_test / cls._blender_cache_path.name)

        # Start Manager
        test_env = os.environ.copy()
        test_env.update({
            "SETHLANS_DB_NAME": str(TEST_DB_NAME),  # Pass absolute path as string
            "DJANGO_SETTINGS_MODULE": "config.settings",
            "SETHLANS_MEDIA_ROOT": str(MEDIA_ROOT_FOR_TEST)
        })
        print("Running migrations...")
        subprocess.run([sys.executable, "manage.py", "migrate"], cwd=PROJECT_ROOT, env=test_env, check=True,
                       capture_output=True)
        print(f"Starting Django manager on port {worker_config.MANAGER_PORT}...")
        manager_command = [sys.executable, "manage.py", "runserver", str(worker_config.MANAGER_PORT), "--noreload"]
        cls.manager_process = subprocess.Popen(manager_command, cwd=PROJECT_ROOT, env=test_env,
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(5)  # Wait for manager to start

        # Create shared test assets with unique names for this test run
        unique_suffix = int(time.time())
        print(f"Creating E2E test project and assets with unique suffix: {unique_suffix}...")
        project_payload = {"name": f"E2E-Test-Project-{unique_suffix}"}
        response = requests.post(f"{MANAGER_URL}/projects/", json=project_payload)
        response.raise_for_status()
        cls.project_id = response.json()['id']
        cls.short_project_id = str(cls.project_id)[:8]

        cls.scene_asset_id = cls._upload_test_asset(f"E2E Test Scene {unique_suffix}",
                                                    worker_config.TEST_BLEND_FILE_PATH, cls.project_id)
        cls.bmw_asset_id = cls._upload_test_asset(f"E2E BMW Scene {unique_suffix}",
                                                  worker_config.BENCHMARK_BLEND_FILE_PATH, cls.project_id)
        cls.anim_asset_id = cls._upload_test_asset(f"E2E Animation Scene {unique_suffix}",
                                                   worker_config.ANIMATION_BLEND_FILE_PATH, cls.project_id)

        # Start Worker
        cls.start_worker(cls.worker_log_queue)

    @classmethod
    def teardown_class(cls):
        """Tear down the environment after all tests in this class have run."""
        print(f"\n--- TEARDOWN: {cls.__name__} ---")

        # Use robust, platform-specific process termination
        if cls.worker_process and cls.worker_process.poll() is None:
            print(f"Terminating worker process (PID: {cls.worker_process.pid})...")
            if platform.system() == "Windows":
                subprocess.run(f"taskkill /F /T /PID {cls.worker_process.pid}", check=False, capture_output=True,
                               shell=True)
            else:
                cls.worker_process.kill()

        if cls.manager_process and cls.manager_process.poll() is None:
            print(f"Terminating manager process (PID: {cls.manager_process.pid})...")
            if platform.system() == "Windows":
                subprocess.run(f"taskkill /F /T /PID {cls.manager_process.pid}", check=False, capture_output=True,
                               shell=True)
            else:
                cls.manager_process.kill()

        # Short delay to allow OS to process termination signals and release file locks
        time.sleep(1)

        if cls.worker_log_thread and cls.worker_log_thread.is_alive():
            cls.worker_log_thread.join(timeout=5)

        # Print captured logs
        print("\n--- CAPTURED WORKER LOGS ---")
        while not cls.worker_log_queue.empty():
            print(f"  [WORKER] {cls.worker_log_queue.get_nowait().strip()}")
        print("--- END OF WORKER LOGS ---\n")

        # Clean up files now that processes are terminated
        print("Cleaning up filesystem artifacts...")
        if os.path.exists(TEST_DB_NAME):
            try:
                os.remove(TEST_DB_NAME)
                print(f"Removed test database: {TEST_DB_NAME}")
            except OSError as e:
                print(f"Error removing test database {TEST_DB_NAME}: {e}")

        # Artifact directories are now cleaned up by session-level hooks.
        # We only clean up class-specific items here.
        if MOCK_TOOLS_DIR.exists():
            shutil.rmtree(MOCK_TOOLS_DIR, ignore_errors=True)

        if os.path.exists(worker_config.BLENDER_VERSIONS_CACHE_FILE):
            os.remove(worker_config.BLENDER_VERSIONS_CACHE_FILE)

        # Clean up mock environment variables to prevent test pollution
        if "SETHLANS_MOCK_CPU_ONLY" in os.environ:
            del os.environ["SETHLANS_MOCK_CPU_ONLY"]

        print("Teardown complete.")