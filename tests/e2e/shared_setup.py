# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2025 Dryad and Naiad Software LLC
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
import logging
from pathlib import Path

import requests

from sethlans_worker_agent import config as worker_config
from sethlans_worker_agent.tool_manager import tool_manager_instance
from sethlans_worker_agent.utils import blender_release_parser, file_operations
from tests.e2e.helpers import is_self_hosted_runner

logger = logging.getLogger(__name__)

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
                logger.error("Asset upload for '%s' failed with status %s.", name, response.status_code)
                logger.error("Response body: %s", response.text)

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
            logger.info("Dynamically determining latest patch for Blender %s.x series...", E2E_BLENDER_SERIES)
            releases = blender_release_parser.get_blender_releases()
            latest_patch_for_series = next((v for v in releases if v.startswith(E2E_BLENDER_SERIES + '.')), None)

            if not latest_patch_for_series:
                raise RuntimeError(f"Cannot find any patch release for Blender series {E2E_BLENDER_SERIES}")

            cls._blender_version_for_test = latest_patch_for_series
            logger.info("Found latest patch: %s", cls._blender_version_for_test)

        cache_root = Path(tempfile.gettempdir()) / "sethlans_e2e_cache"
        cache_root.mkdir(exist_ok=True)
        platform_id = tool_manager_instance._get_platform_identifier()
        blender_install_dir_name = f"blender-{cls._blender_version_for_test}-{platform_id}"
        cls._blender_cache_path = cache_root / blender_install_dir_name

        if cls._blender_cache_path.exists():
            logger.info("Blender %s found in persistent cache: %s", cls._blender_version_for_test, cls._blender_cache_path)
            return

        logger.info("Blender %s not found in cache. Downloading and extracting once...", cls._blender_version_for_test)
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
            logger.info("Successfully cached Blender to %s", cls._blender_cache_path)
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
        that specifically need to validate hardware detection. This fix is also
        bypassed if the `SETHLANS_SELF_HOSTED_RUNNER` environment variable is set.
        """
        logger.info("Starting Worker Agent...")
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

        # This stability fix forces standard (non-self-hosted) macOS CI runners into CPU mode.
        if (platform.system() == "Darwin" and is_ci and not is_self_hosted_runner() and
                cls.__name__ != 'TestWorkerBehavior' and not force_flag_is_set):
            logger.warning("[CI-FIX] Standard macOS CI detected for %s. Forcing worker into CPU-only mode for stability.", cls.__name__)
            test_env["SETHLANS_FORCE_CPU_ONLY"] = "true"
        elif is_self_hosted_runner():
            logger.info("Self-hosted runner detected. Worker will start with full hardware capabilities.")

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

        logger.info("Waiting for worker to complete registration and initial setup...")
        start_time = time.time()
        while time.time() - start_time < 180:
            try:
                line = log_queue.get(timeout=1)
                logger.debug("[SETUP LOG] %s", line.strip())
                if "Loop finished. Sleeping for" in line:
                    logger.info("Worker is ready!")
                    return
            except queue.Empty:
                if cls.worker_process.poll() is not None:
                    raise RuntimeError("Worker process terminated unexpectedly during setup.")
        raise RuntimeError("Worker agent did not become ready within the time limit.")

    @classmethod
    def setup_class(cls):
        """Set up the environment once for all tests in this class."""
        logger.info("--- SETUP: %s ---", cls.__name__)
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
        logger.info("Running migrations...")
        subprocess.run([sys.executable, "manage.py", "migrate"], cwd=PROJECT_ROOT, env=test_env, check=True,
                       capture_output=True)
        logger.info("Starting Django manager on port %s...", worker_config.MANAGER_PORT)
        manager_command = [sys.executable, "manage.py", "runserver", str(worker_config.MANAGER_PORT), "--noreload"]
        cls.manager_process = subprocess.Popen(manager_command, cwd=PROJECT_ROOT, env=test_env,
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(5)  # Wait for manager to start

        # Create shared test assets with unique names for this test run
        unique_suffix = int(time.time())
        logger.info("Creating E2E test project and assets with unique suffix: %s...", unique_suffix)
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
        logger.info("--- TEARDOWN: %s ---", cls.__name__)

        # Use robust, platform-specific process termination
        if cls.worker_process and cls.worker_process.poll() is None:
            logger.info("Terminating worker process (PID: %s)...", cls.worker_process.pid)
            if platform.system() == "Windows":
                subprocess.run(f"taskkill /F /T /PID {cls.worker_process.pid}", check=False, capture_output=True,
                               shell=True)
            else:
                cls.worker_process.kill()

        if cls.manager_process and cls.manager_process.poll() is None:
            logger.info("Terminating manager process (PID: %s)...", cls.manager_process.pid)
            if platform.system() == "Windows":
                subprocess.run(f"taskkill /F /T /PID {cls.manager_process.pid}", check=False, capture_output=True,
                               shell=True)
            else:
                cls.manager_process.kill()

        # Short delay to allow OS to process termination signals and release file locks
        time.sleep(1)

        if cls.worker_log_thread and cls.worker_log_thread.is_alive():
            cls.worker_log_thread.join(timeout=5)

        # Print captured logs from stdout queue
        logger.info("--- CAPTURED WORKER STDOUT ---")
        while not cls.worker_log_queue.empty():
            logger.info("[STDOUT] %s", cls.worker_log_queue.get_nowait().strip())
        logger.info("--- END OF WORKER STDOUT ---")

        # Explicitly read and print the log file for diagnostics
        log_file_path = worker_config.WORKER_LOG_DIR / 'worker.log'
        logger.info("--- READING WORKER LOG FILE: %s ---", log_file_path)
        if log_file_path.exists():
            try:
                with open(log_file_path, 'r', encoding='utf-8', errors='replace') as f:
                    # Log file content line by line for better readability in CI
                    for line in f:
                        logger.info("[LOGFILE] %s", line.strip())
            except Exception as e:
                logger.error("Error reading worker log file: %s", e)
        else:
            logger.warning("Worker log file not found.")
        logger.info("--- END OF WORKER LOG FILE ---")


        # Clean up files now that processes are terminated
        logger.info("Cleaning up filesystem artifacts...")
        if os.path.exists(TEST_DB_NAME):
            try:
                os.remove(TEST_DB_NAME)
                logger.info("Removed test database: %s", TEST_DB_NAME)
            except OSError as e:
                logger.error("Error removing test database %s: %s", TEST_DB_NAME, e)

        # Artifact directories are now cleaned up by session-level hooks.
        # We only clean up class-specific items here.
        if MOCK_TOOLS_DIR.exists():
            shutil.rmtree(MOCK_TOOLS_DIR, ignore_errors=True)

        if os.path.exists(worker_config.BLENDER_VERSIONS_CACHE_FILE):
            os.remove(worker_config.BLENDER_VERSIONS_CACHE_FILE)

        # Clean up mock environment variables to prevent test pollution
        if "SETHLANS_MOCK_CPU_ONLY" in os.environ:
            del os.environ["SETHLANS_MOCK_CPU_ONLY"]

        logger.info("Teardown complete.")