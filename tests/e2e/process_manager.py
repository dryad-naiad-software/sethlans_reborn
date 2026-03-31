# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
#
# Created by Mario Estrella on 8/1/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
"""
Process lifecycle management for the E2E test suite.

This module provides standalone functions for starting and stopping the Django
manager and worker agent processes, caching Blender downloads, and reading
subprocess log output. These are used by BaseE2ETest in shared_setup.py.
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

from sethlans_worker_agent import config as worker_config
from sethlans_worker_agent.tool_manager import tool_manager_instance
from sethlans_worker_agent.utils import blender_release_parser, file_operations
from tests.e2e.helpers import is_self_hosted_runner

logger = logging.getLogger(__name__)

# Re-export constants needed by both modules
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TEST_DB_NAME = PROJECT_ROOT / "test_e2e_db.sqlite3"
MOCK_TOOLS_DIR = Path(worker_config.MANAGED_TOOLS_DIR)
ARTIFACTS_ROOT_FOR_TEST = PROJECT_ROOT / "test_artifacts"
MEDIA_ROOT_FOR_TEST = ARTIFACTS_ROOT_FOR_TEST / "media"
E2E_BLENDER_SERIES = "4.5"


def log_reader_thread(pipe, log_queue):
    """Reads lines from a pipe and puts them in the specified queue."""
    try:
        for line in iter(pipe.readline, ''):
            log_queue.put(line)
    finally:
        pipe.close()


def cache_blender_once(cache_holder):
    """
    Dynamically finds the latest patch for E2E_BLENDER_SERIES, then
    downloads and extracts it to a persistent system temp directory.

    Args:
        cache_holder: An object with ``_blender_version_for_test`` and
            ``_blender_cache_path`` attributes that will be set by this
            function (typically the test class itself).
    """
    if cache_holder._blender_version_for_test is None:
        logger.info(
            "Dynamically determining latest patch for Blender %s.x series...",
            E2E_BLENDER_SERIES,
        )
        releases = blender_release_parser.get_blender_releases()
        latest = next(
            (v for v in releases if v.startswith(E2E_BLENDER_SERIES + '.')),
            None,
        )
        if not latest:
            raise RuntimeError(
                f"Cannot find any patch release for Blender series {E2E_BLENDER_SERIES}"
            )
        cache_holder._blender_version_for_test = latest
        logger.info("Found latest patch: %s", cache_holder._blender_version_for_test)

    cache_root = Path(tempfile.gettempdir()) / "sethlans_e2e_cache"
    cache_root.mkdir(exist_ok=True)
    platform_id = tool_manager_instance._get_platform_identifier()
    blender_dir_name = f"blender-{cache_holder._blender_version_for_test}-{platform_id}"
    cache_holder._blender_cache_path = cache_root / blender_dir_name

    if cache_holder._blender_cache_path.exists():
        logger.info(
            "Blender %s found in persistent cache: %s",
            cache_holder._blender_version_for_test,
            cache_holder._blender_cache_path,
        )
        return

    logger.info(
        "Blender %s not found in cache. Downloading and extracting once...",
        cache_holder._blender_version_for_test,
    )
    releases = blender_release_parser.get_blender_releases()
    release_info = releases.get(
        cache_holder._blender_version_for_test, {}
    ).get(platform_id)
    if not release_info or not all(k in release_info for k in ['url', 'sha256']):
        raise RuntimeError(
            f"Cannot find download info for Blender "
            f"{cache_holder._blender_version_for_test} on {platform_id}"
        )

    try:
        downloaded = file_operations.download_file(release_info['url'], str(cache_root))
        if not file_operations.verify_hash(downloaded, release_info['sha256']):
            raise IOError(f"Hash mismatch for cached Blender download: {downloaded}")
        file_operations.extract_archive(downloaded, str(cache_root))
        file_operations.cleanup_archive(downloaded)
        logger.info("Successfully cached Blender to %s", cache_holder._blender_cache_path)
    except Exception as e:
        if cache_holder._blender_cache_path.exists():
            shutil.rmtree(cache_holder._blender_cache_path)
        cache_holder._blender_cache_path = None
        raise RuntimeError(f"Failed to cache Blender for E2E tests: {e}")


def start_worker(cls, log_queue, extra_env=None):
    """
    Starts the worker process and its log reader thread.

    Applies a stability fix for macOS CI runners by forcing the worker
    into CPU-only mode for most tests. This is disabled for test suites
    that specifically need to validate hardware detection. This fix is
    also bypassed if ``SETHLANS_SELF_HOSTED_RUNNER`` is set.

    Args:
        cls: The test class instance/class — its ``worker_process``,
            ``worker_log_thread``, and ``__name__`` attributes are used.
        log_queue: A ``queue.Queue`` to capture worker stdout.
        extra_env: Optional dict of additional environment variables.
    """
    logger.info("Starting Worker Agent...")
    test_env = os.environ.copy()
    test_env.update({
        "SETHLANS_DB_NAME": str(TEST_DB_NAME),
        "DJANGO_SETTINGS_MODULE": "config.settings",
        "SETHLANS_MEDIA_ROOT": str(MEDIA_ROOT_FOR_TEST),
    })
    if extra_env:
        test_env.update(extra_env)

    is_ci = (
        os.environ.get("CI") == "true"
        or os.environ.get("GITHUB_ACTIONS") == "true"
    )
    force_flag_is_set = (
        "SETHLANS_FORCE_CPU_ONLY" in test_env
        or "SETHLANS_FORCE_GPU_ONLY" in test_env
    )

    if (platform.system() == "Darwin" and is_ci
            and not is_self_hosted_runner()
            and cls.__name__ != 'TestWorkerBehavior'
            and not force_flag_is_set):
        logger.warning(
            "[CI-FIX] Standard macOS CI detected for %s. "
            "Forcing worker into CPU-only mode for stability.",
            cls.__name__,
        )
        test_env["SETHLANS_FORCE_CPU_ONLY"] = "true"
    elif is_self_hosted_runner():
        logger.info(
            "Self-hosted runner detected. Worker will start with full hardware capabilities."
        )

    worker_command = [
        sys.executable, "-m", "sethlans_worker_agent.agent",
        "--loglevel", "DEBUG",
    ]
    cls.worker_process = subprocess.Popen(
        worker_command, cwd=PROJECT_ROOT, env=test_env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding='utf-8', errors='replace',
    )
    cls.worker_log_thread = threading.Thread(
        target=log_reader_thread,
        args=(cls.worker_process.stdout, log_queue),
    )
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
                raise RuntimeError(
                    "Worker process terminated unexpectedly during setup."
                )
    raise RuntimeError("Worker agent did not become ready within the time limit.")


def start_manager():
    """
    Runs Django migrations and starts the manager process.

    Returns:
        subprocess.Popen: The manager process handle.
    """
    test_env = os.environ.copy()
    test_env.update({
        "SETHLANS_DB_NAME": str(TEST_DB_NAME),
        "DJANGO_SETTINGS_MODULE": "config.settings",
        "SETHLANS_MEDIA_ROOT": str(MEDIA_ROOT_FOR_TEST),
    })
    logger.info("Running migrations...")
    subprocess.run(
        [sys.executable, "manage.py", "migrate"],
        cwd=PROJECT_ROOT, env=test_env, check=True, capture_output=True,
    )
    logger.info("Starting Django manager on port %s...", worker_config.MANAGER_PORT)
    manager_command = [
        sys.executable, "manage.py", "runserver",
        str(worker_config.MANAGER_PORT), "--noreload",
    ]
    manager_process = subprocess.Popen(
        manager_command, cwd=PROJECT_ROOT, env=test_env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(5)  # Wait for manager to start
    return manager_process


def terminate_process(process, label="process"):
    """Terminates a subprocess using platform-appropriate methods."""
    if process and process.poll() is None:
        logger.info("Terminating %s (PID: %s)...", label, process.pid)
        if platform.system() == "Windows":
            subprocess.run(
                f"taskkill /F /T /PID {process.pid}",
                check=False, capture_output=True, shell=True,
            )
        else:
            process.kill()
