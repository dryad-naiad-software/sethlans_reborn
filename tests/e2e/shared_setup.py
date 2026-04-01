# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
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
import time
import queue
import logging

import requests

from sethlans_worker_agent import config as worker_config
from tests.e2e.process_manager import (
    TEST_DB_NAME, MOCK_TOOLS_DIR,
    MEDIA_ROOT_FOR_TEST,
    cache_blender_once, start_worker as _start_worker,
    start_manager, terminate_process,
)

# Re-export constants so existing imports from shared_setup keep working
from tests.e2e.process_manager import ARTIFACTS_ROOT_FOR_TEST  # noqa: F401

logger = logging.getLogger(__name__)

MANAGER_URL = worker_config.MANAGER_API_URL.rstrip('/')


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
    def _upload_test_asset(cls, name, file_path, project_id):
        """
        Helper to upload a .blend file asset to the manager.

        Returns:
            int: The ID of the newly created asset.
        """
        with open(str(file_path), 'rb') as f:
            payload = {"name": name, "project": str(project_id)}
            files = {
                "blend_file": (
                    os.path.basename(file_path), f,
                    "application/octet-stream",
                )
            }
            response = requests.post(
                f"{MANAGER_URL}/assets/", data=payload, files=files,
            )
            if response.status_code != 201:
                logger.error(
                    "Asset upload for '%s' failed with status %s.",
                    name, response.status_code,
                )
                logger.error("Response body: %s", response.text)
            response.raise_for_status()
            return response.json()['id']

    @classmethod
    def start_worker(cls, log_queue, extra_env=None):
        """Delegates to process_manager.start_worker."""
        _start_worker(cls, log_queue, extra_env=extra_env)

    @classmethod
    def setup_class(cls):
        """Set up the environment once for all tests in this class."""
        logger.info("--- SETUP: %s ---", cls.__name__)
        if os.path.exists(TEST_DB_NAME):
            os.remove(TEST_DB_NAME)
        if MOCK_TOOLS_DIR.exists():
            shutil.rmtree(MOCK_TOOLS_DIR, ignore_errors=True)
        if os.path.exists(worker_config.BLENDER_VERSIONS_CACHE_FILE):
            os.remove(worker_config.BLENDER_VERSIONS_CACHE_FILE)

        MEDIA_ROOT_FOR_TEST.mkdir(parents=True, exist_ok=True)

        # Prepare Blender installation for the worker
        cache_blender_once(cls)
        blender_dir_for_test = MOCK_TOOLS_DIR / "blender"
        blender_dir_for_test.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            cls._blender_cache_path,
            blender_dir_for_test / cls._blender_cache_path.name,
        )

        # Start Manager
        cls.manager_process = start_manager()

        # Create shared test assets with unique names for this test run
        unique_suffix = int(time.time())
        logger.info(
            "Creating E2E test project and assets with unique suffix: %s...",
            unique_suffix,
        )
        project_payload = {"name": f"E2E-Test-Project-{unique_suffix}"}
        response = requests.post(
            f"{MANAGER_URL}/projects/", json=project_payload,
        )
        response.raise_for_status()
        cls.project_id = response.json()['id']
        cls.short_project_id = str(cls.project_id)[:8]

        cls.scene_asset_id = cls._upload_test_asset(
            f"E2E Test Scene {unique_suffix}",
            worker_config.TEST_BLEND_FILE_PATH, cls.project_id,
        )
        cls.bmw_asset_id = cls._upload_test_asset(
            f"E2E BMW Scene {unique_suffix}",
            worker_config.BENCHMARK_BLEND_FILE_PATH, cls.project_id,
        )
        cls.anim_asset_id = cls._upload_test_asset(
            f"E2E Animation Scene {unique_suffix}",
            worker_config.ANIMATION_BLEND_FILE_PATH, cls.project_id,
        )

        # Start Worker
        cls.start_worker(cls.worker_log_queue)

    @classmethod
    def _print_captured_logs(cls):
        """Print captured worker stdout and log file contents for diagnostics."""
        logger.info("--- CAPTURED WORKER STDOUT ---")
        while not cls.worker_log_queue.empty():
            logger.info("[STDOUT] %s", cls.worker_log_queue.get_nowait().strip())
        logger.info("--- END OF WORKER STDOUT ---")

        log_file_path = worker_config.WORKER_LOG_DIR / 'worker.log'
        logger.info("--- READING WORKER LOG FILE: %s ---", log_file_path)
        if log_file_path.exists():
            try:
                with open(log_file_path, 'r', encoding='utf-8', errors='replace') as f:
                    for line in f:
                        logger.info("[LOGFILE] %s", line.strip())
            except Exception as e:
                logger.error("Error reading worker log file: %s", e)
        else:
            logger.warning("Worker log file not found.")
        logger.info("--- END OF WORKER LOG FILE ---")

    @classmethod
    def _cleanup_filesystem(cls):
        """Remove test database, tools directory, and cache files."""
        logger.info("Cleaning up filesystem artifacts...")
        if os.path.exists(TEST_DB_NAME):
            try:
                os.remove(TEST_DB_NAME)
                logger.info("Removed test database: %s", TEST_DB_NAME)
            except OSError as e:
                logger.error("Error removing test database %s: %s", TEST_DB_NAME, e)

        if MOCK_TOOLS_DIR.exists():
            shutil.rmtree(MOCK_TOOLS_DIR, ignore_errors=True)

        if os.path.exists(worker_config.BLENDER_VERSIONS_CACHE_FILE):
            os.remove(worker_config.BLENDER_VERSIONS_CACHE_FILE)

        if "SETHLANS_MOCK_CPU_ONLY" in os.environ:
            del os.environ["SETHLANS_MOCK_CPU_ONLY"]

    @classmethod
    def teardown_class(cls):
        """Tear down the environment after all tests in this class have run."""
        logger.info("--- TEARDOWN: %s ---", cls.__name__)

        terminate_process(cls.worker_process, "worker process")
        terminate_process(cls.manager_process, "manager process")

        # Short delay to allow OS to release file locks
        time.sleep(1)

        if cls.worker_log_thread and cls.worker_log_thread.is_alive():
            cls.worker_log_thread.join(timeout=5)

        cls._print_captured_logs()
        cls._cleanup_filesystem()

        logger.info("Teardown complete.")
