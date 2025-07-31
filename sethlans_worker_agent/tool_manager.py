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
# Created by Mario Estrella on 07/22/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
# sethlans_worker_agent/tool_manager.py
"""
Manages the discovery, download, and local caching of external tools.

This module is primarily responsible for:
- Scanning for locally installed Blender versions.
- Resolving partial version strings (e.g., '4.5') to the latest available patch.
- Downloading and extracting new Blender versions from official mirrors.
- Verifying downloaded files using SHA256 hashes.
- Providing the correct executable path for a given Blender version.
"""

import logging
import platform
import os
import re
import stat  # <-- ADDED IMPORT
from pathlib import Path
from . import config
from .utils import file_operations, blender_release_parser

logger = logging.getLogger(__name__)


class ToolManager:
    """
    Manages the download, extraction, and path resolution of tools like Blender.

    This class centralizes all the logic for tool management, ensuring a consistent
    and robust approach to handling external dependencies across different operating
    systems and architectures.
    """

    def __init__(self):
        self.tools_dir = Path(config.MANAGED_TOOLS_DIR)
        self.blender_dir = self.tools_dir / "blender"

    def _create_tools_directory_if_not_exists(self):
        """
        Creates the base directory for managed Blender installations if it's missing.
        """
        if not self.blender_dir.exists():
            logger.info(f"Creating managed tools directory at {self.blender_dir}...")
            self.blender_dir.mkdir(parents=True, exist_ok=True)

    def scan_for_local_blenders(self):
        """
        Scans the managed tools directory for already downloaded Blender versions.

        The method expects subdirectories named in the format `blender-X.Y.Z-platform`.
        It verifies that the executable exists before considering an installation valid.

        Returns:
            list: A list of dictionaries, e.g., `[{'version': '4.1.1', 'platform': 'windows-x64'}]`.
        """
        self._create_tools_directory_if_not_exists()
        found_blenders = []
        logger.debug(f"Scanning for local Blender versions in: {self.blender_dir}")

        for subdir in self.blender_dir.iterdir():
            if subdir.is_dir():
                # Expected format: blender-4.1.1-windows-x64
                parts = subdir.name.split('-')
                if len(parts) == 4 and parts[0] == 'blender':
                    version = parts[1]
                    platform_str = f"{parts[2]}-{parts[3]}"

                    exe_path = self._get_executable_path_for_install(subdir.name)
                    if Path(exe_path).is_file():
                        logger.info(f"  Found managed Blender version: {version} for {platform_str}")
                        found_blenders.append({"version": version, "platform": platform_str})
        return found_blenders

    def _get_platform_identifier(self):
        """
        Determines the platform identifier string (e.g., 'windows-x64').

        This is used to match the worker's OS and architecture with the correct
        Blender download file.

        Returns:
            str or None: The platform identifier string, or None if the platform is not supported.
        """
        system = platform.system().lower()
        arch = platform.machine().lower()

        if system == "windows":
            return "windows-x64" if "64" in arch else "windows-x86"
        elif system == "linux":
            if arch == "x86_64":
                return "linux-x64"
            elif arch == "aarch64":
                return "linux-arm64"
        elif system == "darwin":  # macOS
            return "macos-arm64" if "arm" in arch or "aarch64" in arch else "macos-x64"
        return None

    def _get_executable_path_for_install(self, install_dir_name):
        """
        Constructs the full path to the Blender executable within an install folder.

        This handles the different file paths for the Blender executable on
        Windows, Linux, and macOS.

        Args:
            install_dir_name (str): The name of the installation directory (e.g., 'blender-4.1.1-windows-x64').

        Returns:
            str: The full path to the Blender executable.
        """
        base_path = self.blender_dir / install_dir_name
        if platform.system() == "Windows":
            return base_path / "blender.exe"
        elif platform.system() == "Darwin":  # macOS
            # The .app is a directory, so we need to point inside it
            return base_path / "Blender.app" / "Contents" / "MacOS" / "Blender"
        else:  # Linux
            return base_path / "blender"

    def get_blender_executable_path(self, version_str):
        """
        Gets the path to a specific Blender version, assuming it's already installed.

        Args:
            version_str (str): The full version string (e.g., '4.1.1').

        Returns:
            str or None: The absolute path to the Blender executable, or None if not found.
        """
        platform_id = self._get_platform_identifier()
        install_dir_name = f"blender-{version_str}-{platform_id}"
        exe_path = self._get_executable_path_for_install(install_dir_name)

        if Path(exe_path).is_file():
            return str(exe_path)

        logger.debug(f"Executable not found at expected path: {exe_path}")
        return None

    def _get_blender_download_info(self):
        """
        Fetches or loads Blender download information from cache or the web.

        First, it checks for a local cache file. If the cache is not found or is
        invalid, it scrapes the official Blender download site for all available
        releases and saves the data to a local cache file for future use.

        Returns:
            dict: A dictionary mapping Blender version strings to their download
                  information (URL, SHA256 hash).
        """
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
        """
        Resolves a full X.Y.Z version from a partial X.Y version string.

        It prioritizes finding the latest patch version that is already
        locally installed. If no local versions match, it falls back to
        checking the latest available version on the web.

        Args:
            requested_version (str): The requested version, either in full
                                     ('4.5.1') or partial ('4.5') format.

        Returns:
            str or None: The full version string, or None if no matching
                         version can be resolved.
        """
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
        """
        Ensures the requested Blender version is available, downloading and
        installing it if necessary.

        This is the main public method of the `ToolManager` class. It orchestrates
        the entire process: resolving the version, checking local installations,
        downloading from the web, verifying the integrity with a SHA256 hash,
        and finally extracting the archive and setting permissions.

        Args:
            requested_version (str): The requested Blender version (e.g., '4.5' or '4.5.1').

        Returns:
            str or None: The absolute path to the Blender executable if successful,
                         otherwise None.
        """
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

        # 3. Download, Verify, and Extract
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

            # Only extract if hash is present and verified
            file_operations.extract_archive(download_path, self.blender_dir)
            file_operations.cleanup_archive(download_path)

        except Exception as e:
            logger.critical(f"An error occurred during download/extraction: {e}", exc_info=True)
            return None

        # --- 4. NEW: Verify path and set permissions ---
        final_exe_path = self.get_blender_executable_path(full_version)
        if final_exe_path and platform.system() != "Windows":
            logger.info(f"Setting execute permission on {final_exe_path}")
            # Get current permissions and add execute bit for owner
            st = os.stat(final_exe_path)
            os.chmod(final_exe_path, st.st_mode | stat.S_IEXEC)

        return final_exe_path


# Singleton instance
tool_manager_instance = ToolManager()