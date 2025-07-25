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

# sethlans_worker_agent/tool_manager.py

import logging
import platform
import os
from pathlib import Path
from . import config
from .utils import file_operations, blender_release_parser

logger = logging.getLogger(__name__)


class ToolManager:
    """Manages the download, extraction, and path resolution of tools like Blender."""

    def __init__(self):
        self.tools_dir = Path(config.MANAGED_TOOLS_DIR)
        self.blender_dir = self.tools_dir / "blender"

    def _create_tools_directory_if_not_exists(self):
        """Creates the base directory for managed Blender installations if it's missing."""
        if not self.blender_dir.exists():
            logger.info(f"Creating managed tools directory at {self.blender_dir}...")
            self.blender_dir.mkdir(parents=True, exist_ok=True)

    def scan_for_local_blenders(self):
        """
        Scans the managed tools directory for already downloaded Blender versions.
        Returns a list of dictionaries, e.g., [{'version': '4.1.1', 'platform': 'windows-x64'}]
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
        """Determines the platform identifier (e.g., 'windows-x64')."""
        system = platform.system().lower()
        arch = platform.machine().lower()

        if system == "windows":
            return "windows-x64" if "64" in arch else "windows-x86"
        elif system == "linux":
            return "linux-x64"
        elif system == "darwin":  # macOS
            return "macos-arm64" if "arm" in arch or "aarch64" in arch else "macos-x64"
        return None

    def _get_executable_path_for_install(self, install_dir_name):
        """Constructs the full path to the blender executable within an install folder."""
        base_path = self.blender_dir / install_dir_name
        if platform.system() == "Windows":
            return base_path / "blender.exe"
        elif platform.system() == "Darwin":  # macOS
            return base_path / "Blender.app" / "Contents" / "MacOS" / "Blender"
        else:  # Linux
            return base_path / "blender"

    def get_blender_executable_path(self, version_str):
        """Gets the path to a specific Blender version, assuming it's already installed."""
        platform_id = self._get_platform_identifier()
        install_dir_name = f"blender-{version_str}-{platform_id}"
        exe_path = self._get_executable_path_for_install(install_dir_name)

        if Path(exe_path).is_file():
            return str(exe_path)

        logger.debug(f"Executable not found at expected path: {exe_path}")
        return None

    def _get_blender_download_info(self):
        """Fetches or loads Blender download info from cache or web."""
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

    def ensure_blender_version_available(self, requested_version):
        """
        Ensures the requested Blender version is available. Downloads if necessary.
        Returns the path to the executable if available, otherwise None.
        """
        self._create_tools_directory_if_not_exists()
        logger.info(f"Checking for Blender version {requested_version} availability.")

        # 1. Check if it's already installed
        exe_path = self.get_blender_executable_path(requested_version)
        if exe_path:
            logger.info(f"Blender version {requested_version} already available locally. Path: {exe_path}")
            return exe_path

        # 2. If not, find download URL
        logger.info(f"Version {requested_version} not found locally. Attempting to download.")
        blender_releases = self._get_blender_download_info()
        platform_id = self._get_platform_identifier()

        release_info = blender_releases.get(requested_version, {}).get(platform_id)
        if not release_info:
            logger.error(f"Could not find release info for Blender {requested_version} on platform {platform_id}.")
            return None

        url = release_info.get('url')
        expected_hash = release_info.get('sha256')

        if not url:
            logger.error(f"Could not find a download URL for Blender {requested_version} on platform {platform_id}.")
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

        # 4. Verify and return the new path
        return self.get_blender_executable_path(requested_version)


# Singleton instance
tool_manager_instance = ToolManager()