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
from pathlib import Path

# Need to import these to access the paths
from tests.e2e.shared_setup import ARTIFACTS_ROOT_FOR_TEST
from sethlans_worker_agent import config as worker_config


def pytest_sessionstart(session):
    """
    Pytest hook that runs once at the beginning of the entire test session.

    This is used to clean up artifact directories from any previous runs to
    ensure a clean slate before any tests start.
    """
    print("\n--- E2E Session Setup: Cleaning up old artifacts ---")
    paths_to_clean = [
        ARTIFACTS_ROOT_FOR_TEST,
        worker_config.WORKER_OUTPUT_DIR
    ]
    for path in paths_to_clean:
        if path.exists():
            try:
                shutil.rmtree(path)
                print(f"Successfully removed old artifact directory: {path}")
            except Exception as e:
                print(f"ERROR: Could not remove old artifact directory {path}: {e}")


def pytest_sessionfinish(session, exitstatus):
    """
    Pytest hook that runs once at the end of the entire test session.

    This is used to clean up persistent caches and, for local runs, the
    artifact directories generated during the test.
    """
    # --- Cleanup for Blender Cache ---
    cache_root = Path(tempfile.gettempdir()) / "sethlans_e2e_cache"
    print(f"\n--- E2E Session Teardown: Cleaning up cache at {cache_root} ---")
    if cache_root.exists():
        try:
            shutil.rmtree(cache_root)
            print(f"Successfully removed persistent cache directory: {cache_root}")
        except Exception as e:
            print(f"ERROR: Could not remove persistent cache directory {cache_root}: {e}")

    # --- Conditional Cleanup for Test-Generated Artifacts ---
    is_ci = os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true"
    if not is_ci:
        print("\n--- E2E Session Teardown: Cleaning up artifacts for local run ---")
        paths_to_clean = [
            ARTIFACTS_ROOT_FOR_TEST,
            worker_config.WORKER_OUTPUT_DIR
        ]
        for path in paths_to_clean:
            if path.exists():
                try:
                    shutil.rmtree(path)
                    print(f"Successfully removed artifact directory: {path}")
                except Exception as e:
                    print(f"ERROR: Could not remove artifact directory {path}: {e}")
    else:
        print("\n--- E2E Session Teardown: Preserving artifacts for CI upload ---")