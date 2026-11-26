# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Manages local Blender installs: discovery, version resolution and removal.

Includes reference counting to prevent version cleanup from deleting
directories while renders are in progress. Installing a version is
``blender_install.install`` (BS-WK-1, BS-WK-3); the job path only looks up
installed versions (BS-WK-11).
"""

import logging
import re
import shutil
import time
import threading
from pathlib import Path
from shared.blender_sources import BlenderSourcesError, current_platform_id, is_version, latest_patch
from . import config
from .platform_utils import get_executable_path_for_blender

logger = logging.getLogger(__name__)


class ToolManager:
    """Manages the download, extraction, and path resolution of Blender."""

    def __init__(self):
        self.tools_dir = Path(config.MANAGED_TOOLS_DIR)
        self.blender_dir = self.tools_dir / "blender"
        # Reference counting for safe version cleanup.
        # Tracks how many active renders are using each version.
        self._version_usage = {}  # {version_str: int}
        self._usage_lock = threading.Lock()
        # Tracks versions currently being downloaded/extracted.
        # scan_for_local_blenders() excludes these to prevent reporting
        # partially-extracted directories as available (P2-F5).
        self._downloading_versions = set()
        self._download_lock = threading.Lock()
        self._scan_cache, self._scan_cache_time = None, 0.0

    def acquire_version(self, version):
        """Increment usage count for a Blender version before render.

        Must be called before starting a render subprocess. The
        corresponding release_version() call must be in a finally block.
        """
        with self._usage_lock:
            self._version_usage[version] = self._version_usage.get(version, 0) + 1
            logger.debug(f"Acquired version {version}. Usage count: {self._version_usage[version]}")

    def release_version(self, version):
        """Decrement usage count for a Blender version after render.

        Must be called in a finally block after the render completes
        or fails to prevent version leak.
        """
        with self._usage_lock:
            count = self._version_usage.get(version, 0) - 1
            if count <= 0:
                self._version_usage.pop(version, None)
            else:
                self._version_usage[version] = count
            logger.debug(f"Released version {version}. Usage count: {max(0, count)}")

    def is_version_in_use(self, version):
        """Check if a Blender version has active renders using it."""
        with self._usage_lock:
            return self._version_usage.get(version, 0) > 0

    def _create_tools_directory_if_not_exists(self):
        """Create the base directory for managed Blender installations if missing."""
        if not self.blender_dir.exists():
            logger.info(f"Creating managed tools directory at {self.blender_dir}...")
            self.blender_dir.mkdir(parents=True, exist_ok=True)

    def scan_for_local_blenders(self):
        """Scan for installed Blender versions, excluding those mid-download."""
        if self._scan_cache is not None and (time.time() - self._scan_cache_time) < 30:
            found = list(self._scan_cache)
        else:
            self._create_tools_directory_if_not_exists()
            found = []
            logger.debug(f"Scanning for local Blender versions in: {self.blender_dir}")
            for subdir in self.blender_dir.iterdir():
                if subdir.is_dir():
                    parts = subdir.name.split('-')
                    if len(parts) == 4 and parts[0] == 'blender':
                        version = parts[1]
                        platform_str = f"{parts[2]}-{parts[3]}"
                        exe_path = self._get_executable_path_for_install(subdir.name)
                        if Path(exe_path).is_file():
                            logger.debug(f"  Found managed Blender version: {version} for {platform_str}")
                            found.append({"version": version, "platform": platform_str})
            self._scan_cache, self._scan_cache_time = found, time.time()
            found = list(found)
        # Always apply download exclusion filter (P2-F5).
        with self._download_lock:
            downloading = set(self._downloading_versions)
        return [b for b in found if b['version'] not in downloading]

    def _get_platform_identifier(self):
        """Return the platform identifier string (e.g., 'windows-x64'), or None (BS-SRC-6)."""
        return current_platform_id()

    def _get_executable_path_for_install(self, install_dir_name):
        """Return the full path to the Blender executable in an install folder."""
        return get_executable_path_for_blender(self.blender_dir, install_dir_name)

    def get_blender_executable_path(self, version_str):
        """Return the executable path for an installed ``X.Y.Z`` version, or None.

        None while the version is being installed (``_downloading_versions``),
        so no caller outside the install sees a half-extracted folder.
        """
        if not is_version(version_str):
            return None
        with self._download_lock:
            if version_str in self._downloading_versions:
                return None
        return self._installed_exe_path(version_str)

    def _installed_exe_path(self, version_str):
        """The executable path when its file exists, ignoring an install in progress.

        Only ``blender_install`` uses this, while it holds the version's lock.
        """
        if not is_version(version_str):
            return None
        platform_id = self._get_platform_identifier()
        install_dir_name = f"blender-{version_str}-{platform_id}"
        exe_path = self._get_executable_path_for_install(install_dir_name)

        if Path(exe_path).is_file():
            return str(exe_path)

        logger.debug(f"Executable not found at expected path: {exe_path}")
        return None

    def _resolve_version(self, requested_version):
        """Resolve a partial X.Y version to full X.Y.Z, or pass through X.Y.Z."""
        if re.fullmatch(r'\d+\.\d+\.\d+', requested_version):
            return requested_version  # Already a full version

        if not re.fullmatch(r'\d+\.\d+', requested_version):
            logger.error(f"Invalid version format: '{requested_version}'. Must be 'X.Y' or 'X.Y.Z'.")
            return None

        # Check for local installations first
        local_blenders = self.scan_for_local_blenders()
        matching_patches = [
            b['version'] for b in local_blenders
            if b['version'].startswith(requested_version + '.')
        ]
        if matching_patches:
            latest_local = sorted(matching_patches, key=lambda v: [int(p) for p in v.split('.')], reverse=True)[0]
            logger.info(f"Resolved '{requested_version}' to latest local version: {latest_local}")
            return latest_local

        # Not installed: ask the sources for the latest patch (BS-WK-2).
        try:
            latest_web = latest_patch(requested_version)
        except (BlenderSourcesError, ValueError) as e:
            logger.error(f"Could not find any patch versions for series '{requested_version}': {e}")
            return None
        logger.info(f"Resolved '{requested_version}' to latest available web version: {latest_web}")
        return latest_web

    def remove_blender_version(self, version_str):
        """
        Remove a specific Blender version's installation directory (P4-F4).

        Refuses to delete if the version is currently in use by an
        active render (reference count > 0), preventing the TOCTOU
        race between cleanup and job start.

        Args:
            version_str: Full version string (e.g., '4.2.19').

        Returns:
            True if the directory was removed, False otherwise.
        """
        if self.is_version_in_use(version_str):
            logger.warning(
                f"Cannot remove Blender {version_str}: "
                f"version is currently in use by an active render."
            )
            return False

        platform_id = self._get_platform_identifier()
        if not platform_id:
            logger.error("Cannot determine platform for version removal.")
            return False

        install_dir = self.blender_dir / f"blender-{version_str}-{platform_id}"
        if not install_dir.exists():
            logger.debug(f"Directory does not exist, nothing to remove: {install_dir}")
            return False

        # Validate the path stays within blender_dir to prevent traversal.
        resolved = install_dir.resolve()
        if not str(resolved).startswith(str(self.blender_dir.resolve())):
            logger.error(f"Path traversal detected: {install_dir}")
            return False

        try:
            shutil.rmtree(install_dir)
            self._scan_cache = None
            logger.info(f"Removed Blender version directory: {install_dir}")
            return True
        except OSError as e:
            logger.error(f"Failed to remove Blender directory {install_dir}: {e}")
            return False


# Singleton instance
tool_manager_instance = ToolManager()
