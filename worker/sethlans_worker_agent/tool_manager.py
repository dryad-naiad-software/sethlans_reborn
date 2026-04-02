# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Manages Blender version discovery, download, and local caching.

Includes reference counting to prevent version cleanup from deleting
directories while renders are in progress.
"""

import logging
import platform
import os
import re
import shutil
import stat
import time
import threading
from pathlib import Path
from . import config
from .utils import file_operations, blender_release_parser
from .platform_utils import get_platform_identifier, get_executable_path_for_blender

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
        """Return the platform identifier string (e.g., 'windows-x64')."""
        return get_platform_identifier()

    def _get_executable_path_for_install(self, install_dir_name):
        """Return the full path to the Blender executable in an install folder."""
        return get_executable_path_for_blender(self.blender_dir, install_dir_name)

    def get_blender_executable_path(self, version_str):
        """Return the executable path for an installed version, or None."""
        platform_id = self._get_platform_identifier()
        install_dir_name = f"blender-{version_str}-{platform_id}"
        exe_path = self._get_executable_path_for_install(install_dir_name)

        if Path(exe_path).is_file():
            return str(exe_path)

        logger.debug(f"Executable not found at expected path: {exe_path}")
        return None

    def _get_blender_download_info(self):
        """Fetch or load Blender download info from cache or the web."""
        if os.path.exists(config.BLENDER_VERSIONS_CACHE_FILE):
            try:
                with open(config.BLENDER_VERSIONS_CACHE_FILE, 'r') as f:
                    logger.debug("Loading Blender download info from cache.")
                    return file_operations.load_json(f)
            except Exception as e:
                logger.warning(f"Could not load Blender versions cache file: {e}. Refetching.")

        info = blender_release_parser.get_blender_releases()

        with open(config.BLENDER_VERSIONS_CACHE_FILE, 'w') as f:
            file_operations.dump_json(info, f)
            logger.info(f"Saved generated Blender download info to local cache: {config.BLENDER_VERSIONS_CACHE_FILE}.")
        return info

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

        # If not found locally, check the web for the latest patch
        all_releases = self._get_blender_download_info()
        matching_web_patches = [v for v in all_releases if v.startswith(requested_version + '.')]
        if matching_web_patches:
            latest_web = sorted(matching_web_patches, key=lambda v: [int(p) for p in v.split('.')], reverse=True)[0]
            logger.info(f"Resolved '{requested_version}' to latest available web version: {latest_web}")
            return latest_web

        logger.error(f"Could not find any patch versions for series '{requested_version}'.")
        return None

    def ensure_blender_version_available(self, requested_version):
        """Ensure a Blender version is installed, downloading if necessary."""
        self._create_tools_directory_if_not_exists()

        full_version = self._resolve_version(requested_version)
        if not full_version:
            return None

        logger.info(f"Checking for Blender version {full_version} availability.")

        # 1. Check if it's already installed
        exe_path = self.get_blender_executable_path(full_version)
        if exe_path:
            logger.info(f"Blender version {full_version} already available locally. Path: {exe_path}")
            return exe_path

        # 2. If not, find download URL
        logger.info(f"Version {full_version} not found locally. Attempting to download.")

        with self._download_lock:  # Mark as downloading so scan excludes it (P2-F5).
            self._downloading_versions.add(full_version)
        self._scan_cache = None
        try:
            result = self._download_and_install(full_version)
        finally:
            with self._download_lock:
                self._downloading_versions.discard(full_version)

        return result

    def _download_and_install(self, full_version):
        """Download, verify, extract, and set permissions for a Blender version."""
        blender_releases = self._get_blender_download_info()
        platform_id = self._get_platform_identifier()

        release_info = blender_releases.get(full_version, {}).get(platform_id)
        if not release_info:
            logger.error(f"Could not find release info for Blender {full_version} on platform {platform_id}.")
            return None

        url = release_info.get('url')
        expected_hash = release_info.get('sha256')

        if not url:
            logger.error(f"Could not find a download URL for Blender {full_version} on platform {platform_id}.")
            return None

        try:
            download_path = file_operations.download_file(url, self.blender_dir)

            if not expected_hash:
                logger.error(
                    f"No SHA256 hash found for {os.path.basename(download_path)}. Deleting unverified file for security.")
                os.remove(download_path)
                return None

            if not file_operations.verify_hash(download_path, expected_hash):
                logger.error("Hash verification failed. Deleting corrupt file.")
                os.remove(download_path)
                return None

            file_operations.extract_archive(download_path, self.blender_dir)
            file_operations.cleanup_archive(download_path)
            self._scan_cache = None

        except Exception as e:
            logger.critical(f"An error occurred during download/extraction: {e}", exc_info=True)
            return None

        final_exe_path = self.get_blender_executable_path(full_version)
        if final_exe_path and platform.system() != "Windows":
            logger.info(f"Setting execute permission on {final_exe_path}")
            st = os.stat(final_exe_path)
            os.chmod(final_exe_path, st.st_mode | stat.S_IEXEC)

        return final_exe_path

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
