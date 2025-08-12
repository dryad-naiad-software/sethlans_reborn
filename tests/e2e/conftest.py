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
Pytest configuration and hooks for the E2E test suite.

This file provides session-level setup and teardown logic that applies to the
entire E2E test run, not just a single test class.
"""
import os
import shutil
import tempfile
import logging
from pathlib import Path

# Need to import these to access the paths
from tests.e2e.shared_setup import ARTIFACTS_ROOT_FOR_TEST
from sethlans_worker_agent import config as worker_config

logger = logging.getLogger(__name__)


def pytest_sessionstart(session):
    """
    Pytest hook that runs once at the beginning of the entire test session.

    This is used to clean up artifact directories from any previous runs to
    ensure a clean slate before any tests start.
    """
    logger.info("--- E2E Session Setup: Cleaning up old artifacts ---")
    paths_to_clean = [
        ARTIFACTS_ROOT_FOR_TEST,
        worker_config.WORKER_OUTPUT_DIR
    ]
    for path in paths_to_clean:
        if path.exists():
            try:
                shutil.rmtree(path)
                logger.info("Successfully removed old artifact directory: %s", path)
            except Exception as e:
                logger.error("Could not remove old artifact directory %s: %s", path, e)


def pytest_sessionfinish(session, exitstatus):
    """
    Pytest hook that runs once at the end of the entire test session.

    This is used to clean up persistent caches and, for local runs, the
    artifact directories generated during the test.
    """
    # --- Cleanup for Blender Cache ---
    cache_root = Path(tempfile.gettempdir()) / "sethlans_e2e_cache"
    logger.info("--- E2E Session Teardown: Cleaning up cache at %s ---", cache_root)
    if cache_root.exists():
        try:
            shutil.rmtree(cache_root)
            logger.info("Successfully removed persistent cache directory: %s", cache_root)
        except Exception as e:
            logger.error("Could not remove persistent cache directory %s: %s", cache_root, e)

    # --- Conditional Cleanup for Test-Generated Artifacts ---
    is_ci = os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true"
    if not is_ci:
        logger.info("--- E2E Session Teardown: Cleaning up artifacts for local run ---")
        paths_to_clean = [
            ARTIFACTS_ROOT_FOR_TEST,
            worker_config.WORKER_OUTPUT_DIR
        ]
        for path in paths_to_clean:
            if path.exists():
                try:
                    shutil.rmtree(path)
                    logger.info("Successfully removed artifact directory: %s", path)
                except Exception as e:
                    logger.error("Could not remove artifact directory %s: %s", path, e)
    else:
        logger.info("--- E2E Session Teardown: Preserving artifacts for CI upload ---")