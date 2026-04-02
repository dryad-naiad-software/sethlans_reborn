# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Session-scoped pytest fixtures for E2E tests.

Provides Blender cache management and session-wide cleanup of
temporary artifacts (databases, rendered output, Blender installs).
"""

import logging
import platform
import shutil
import tempfile
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

# Persistent cache directory across pytest sessions to avoid
# re-downloading Blender on every run.
_CACHE_DIR_NAME = "sethlans_e2e_cache"


@pytest.fixture(scope="session")
def blender_cache_dir():
    """
    Session-scoped fixture providing a persistent Blender cache directory.

    The cache lives under the system temp directory and persists across
    pytest sessions so Blender is downloaded at most once.

    Yields:
        pathlib.Path: Path to the Blender cache directory.
    """
    cache_dir = Path(tempfile.gettempdir()) / _CACHE_DIR_NAME
    cache_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Blender cache directory: %s", cache_dir)
    yield cache_dir
    # Do NOT delete the cache — it persists across sessions.


@pytest.fixture(scope="session")
def e2e_temp_root():
    """
    Session-scoped temp root for all E2E test class temp directories.

    Cleaned up after the full suite completes.

    Yields:
        pathlib.Path: Root temp directory for this pytest session.
    """
    root = Path(tempfile.mkdtemp(prefix="sethlans_e2e_"))
    logger.info("E2E session temp root: %s", root)
    yield root
    # Clean up the session temp directory after all tests.
    try:
        if platform.system() == "Windows":
            # Windows may hold file locks; retry with on_error handler.
            def _remove_readonly(func, path, _exc_info):
                import os
                import stat
                os.chmod(path, stat.S_IWRITE)
                func(path)
            shutil.rmtree(root, onerror=_remove_readonly)
        else:
            shutil.rmtree(root)
        logger.info("Cleaned up E2E session temp root: %s", root)
    except Exception as exc:
        logger.warning(
            "Failed to clean up E2E temp root %s: %s", root, exc
        )
