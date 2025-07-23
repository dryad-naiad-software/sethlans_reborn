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

import os
import re
import requests
import json
import datetime
import platform
import hashlib
import logging  # <-- NEW IMPORT

from bs4 import BeautifulSoup
from urllib.parse import urljoin

from . import config
from .utils.file_hasher import calculate_file_sha256
from .utils.file_operations import download_file, extract_zip_file
from .utils.blender_release_parser import fetch_page_soup, parse_major_version_directories, \
    get_sha256_hash_for_zip, collect_blender_version_details

# Get a logger for this module
logger = logging.getLogger(__name__)


# Class to encapsulate tool management logic and its cache
class ToolManager:
    _instance = None  # Singleton instance to ensure only one ToolManager object

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ToolManager, cls).__new__(cls)
            cls._instance.CACHED_BLENDER_DOWNLOAD_INFO = []  # Initialize cache on the instance
            logger.info("Initializing ToolManager instance.")  # <-- Changed print to logger.info
        return cls._instance

    def scan_for_blender_versions(self):
        """
        Scans the managed_tools directory for installed Blender versions, filtering for 4.x and greater,
        and matching the current OS.
        Returns a dictionary like {'blender': ['4.0.0', '4.1.0']}
        """
        blender_versions_found = []
        blender_path = os.path.join(config.MANAGED_TOOLS_DIR, 'blender')

        current_os_download_suffix = config.CURRENT_PLATFORM_BLENDER_DETAILS.get(
            'download_suffix') if config.CURRENT_PLATFORM_BLENDER_DETAILS else None

        if os.path.exists(blender_path) and os.path.isdir(blender_path):
            logger.debug(f"Scanning for local Blender versions in: {blender_path}")  # <-- Changed print to logger.debug
            for item_name in os.listdir(blender_path):
                full_path = os.path.join(blender_path, item_name)

                if os.path.isdir(full_path):
                    version_platform_match = re.search(r'blender-(\d+\.\d+\.\d+)-(.+)', item_name)
                    if version_platform_match:
                        version_str = version_platform_match.group(1)
                        platform_suffix_found = version_platform_match.group(2)

                        if current_os_download_suffix and platform_suffix_found == current_os_download_suffix:
                            blender_exe_path = self._get_managed_blender_executable_full_path(item_name)

                            if blender_exe_path:
                                try:
                                    major_version = int(version_str.split('.')[0])
                                    if major_version >= 4:
                                        blender_versions_found.append(version_str)
                                        logger.info(
                                            f"  Found managed Blender version: {version_str} for {platform_suffix_found} at {full_path}")  # <-- Changed print to logger.info
                                    else:
                                        logger.debug(
                                            f"Skipping managed Blender version < 4.x: {version_str}")  # <-- Changed print to logger.debug
                                except ValueError:
                                    logger.warning(
                                        f"Could not parse major version from '{version_str}'. Skipping.")  # <-- Changed print to logger.warning
                            else:
                                logger.warning(
                                    f"Skipping folder '{item_name}': No recognized blender executable found for current OS.")  # <-- Changed print to logger.warning
                        else:
                            logger.debug(
                                f"Skipping managed Blender folder '{item_name}': Platform suffix '{platform_suffix_found}' does not match current OS '{current_os_download_suffix}'.")  # <-- Changed print to logger.debug
                    else:
                        logger.warning(
                            f"Could not extract version/platform from folder name '{item_name}'. Skipping.")  # <-- Changed print to logger.warning
                else:
                    logger.debug(f"Skipping item '{item_name}': Not a directory.")  # <-- Changed print to logger.debug

        blender_versions_found.sort()

        available_tools = {}
        if blender_versions_found:
            available_tools['blender'] = blender_versions_found
        else:
            logger.info(
                f"No Blender versions 4.x+ found for {platform.system()} {platform.machine().lower()} in {blender_path}.")  # <-- Changed print to logger.info

        return available_tools

    def _load_blender_cache(self):
        """Helper to load Blender download info from the local JSON cache file."""
        if os.path.exists(config.BLENDER_VERSIONS_CACHE_FILE):
            try:
                with open(config.BLENDER_VERSIONS_CACHE_FILE, 'r') as f:
                    loaded_data_list = json.load(f)
                    self.CACHED_BLENDER_DOWNLOAD_INFO = loaded_data_list
                    logger.info(
                        f"Loaded Blender download info from local cache: {config.BLENDER_VERSIONS_CACHE_FILE}.")  # <-- Changed print to logger.info
                    return True
            except json.JSONDecodeError:
                logger.warning(
                    f"Blender versions cache file corrupted. Will re-generate.")  # <-- Changed print to logger.warning
                self.CACHED_BLENDER_DOWNLOAD_INFO = []
            except Exception as e:
                logger.warning(
                    f"Failed to load Blender versions cache: {e}. Will re-generate.")  # <-- Changed print to logger.warning
                self.CACHED_BLENDER_DOWNLOAD_INFO = []
        return False

    def _save_blender_cache(self, data_list):
        """Helper to save Blender download info to the local JSON cache file."""
        try:
            os.makedirs(os.path.dirname(config.BLENDER_VERSIONS_CACHE_FILE), exist_ok=True)
            json.dump(data_list, open(config.BLENDER_VERSIONS_CACHE_FILE, 'w'), indent=4)
            logger.info(
                f"Saved generated Blender download info to local cache: {config.BLENDER_VERSIONS_CACHE_FILE}.")  # <-- Changed print to logger.info
            return True
        except Exception as e:
            logger.error(f"Failed to save Blender versions cache: {e}")  # <-- Changed print to logger.error
            return False

    def _filter_and_process_major_minor_versions(self, versions_by_major_minor_raw):
        """
        Takes raw collected version data (grouped by major.minor) and filters down
        to the latest patch for each (major.minor, platform_suffix) combination.
        Returns a LIST of dictionaries, where each dict represents the latest version
        for a specific major.minor AND platform.
        """
        final_list_of_versions = []

        for major_minor_key, versions_for_major_minor in versions_by_major_minor_raw.items():
            versions_by_platform_suffix = {}
            for version_info in versions_for_major_minor:
                platform_suffix = version_info.get('platform_suffix')
                if platform_suffix not in versions_by_platform_suffix:
                    versions_by_platform_suffix[platform_suffix] = []
                versions_by_platform_suffix[platform_suffix].append(version_info)

            for platform_suffix_key, versions_list_for_platform in versions_by_platform_suffix.items():
                versions_list_for_platform.sort(key=lambda x: [int(v) for v in x['version'].split('.')], reverse=True)

                if versions_list_for_platform:
                    latest_version_for_platform = versions_list_for_platform[0]
                    final_list_of_versions.append(latest_version_for_platform)
                    logger.info(
                        f"  Selected latest for {major_minor_key} series and platform '{platform_suffix_key}': {latest_version_for_platform['version']}")  # <-- Changed print to logger.info
                else:
                    logger.debug(
                        f"No versions found for {major_minor_key} series and platform '{platform_suffix_key}'.")  # <-- Changed print to logger.debug

        return final_list_of_versions

    def generate_and_cache_blender_download_info(self):
        """
        Orchestrates the generation of comprehensive Blender download info.
        Tries to load from cache, otherwise performs dynamic discovery, filters, and saves.
        Returns a dictionary mapping version string to its download info dictionary.
        This dict will be filtered for the CURRENT OS, for easier lookup by ensure_blender_version_available().
        """
        # Try to load from cache file first
        if self._load_blender_cache():
            logger.info(
                "Using in-memory cache for Blender download info (loaded from file).")  # <-- Changed print to logger.info
            # Filter the loaded cache to only include entries for the current OS
            current_platform_suffix = config.CURRENT_PLATFORM_BLENDER_DETAILS.get(
                'download_suffix') if config.CURRENT_PLATFORM_BLENDER_DETAILS else None
            if not current_platform_suffix:
                logger.error(
                    f"Cannot filter cache for unsupported OS: {platform.system()} {platform.machine().lower()}.")  # <-- Changed print to logger.error
                return {}

            filtered_cache_for_current_os = {}
            for entry in self.CACHED_BLENDER_DOWNLOAD_INFO:
                if entry.get('platform_suffix') == current_platform_suffix:
                    filtered_cache_for_current_os[entry['version']] = entry
            self.CACHED_BLENDER_DOWNLOAD_INFO = filtered_cache_for_current_os
            return self.CACHED_BLENDER_DOWNLOAD_INFO

        logger.info(
            "Performing dynamic Blender download info generation (4.x+ only) with mirrors...")  # <-- Changed print to logger.info

        versions_by_major_minor = {}

        try:
            main_soup = fetch_page_soup(config.BLENDER_RELEASES_URL)
            if not main_soup:
                logger.error("Could not fetch main Blender releases page.")  # <-- Changed print to logger.error
                return {}

            major_version_dir_urls = parse_major_version_directories(main_soup)

            for major_version_dir_url_blender_org in major_version_dir_urls:
                versions_from_dir = collect_blender_version_details(major_version_dir_url_blender_org)

                for version_data in versions_from_dir:
                    major_minor_key = ".".join(version_data['version'].split('.')[:2])
                    if major_minor_key not in versions_by_major_minor:
                        versions_by_major_minor[major_minor_key] = []
                    versions_by_major_minor[major_minor_key].append(version_data)

                if not versions_from_dir:
                    logger.info(
                        f"No 4.x+ zip/tar.xz/dmg files found in {major_version_dir_url_blender_org}")  # <-- Changed print to logger.info

        except Exception as e:
            logger.error(
                f"An unexpected error occurred during URL discovery/generation: {e}")  # <-- Changed print to logger.error
            return {}

        generated_info_list_all_platforms = self._filter_and_process_major_minor_versions(versions_by_major_minor)

        self._save_blender_cache(generated_info_list_all_platforms)

        current_platform_suffix = config.CURRENT_PLATFORM_BLENDER_DETAILS.get(
            'download_suffix') if config.CURRENT_PLATFORM_BLENDER_DETAILS else None

        if not current_platform_suffix:
            logger.error(
                f"Cannot filter generated info for unsupported OS: {platform.system()} {platform.machine().lower()}.")  # <-- Changed print to logger.error
            return {}

        final_info_for_current_os = {}
        for entry_data in generated_info_list_all_platforms:
            if entry_data.get('platform_suffix') == current_platform_suffix:
                final_info_for_current_os[entry_data['version']] = entry_data

        self.CACHED_BLENDER_DOWNLOAD_INFO = final_info_for_current_os
        return final_info_for_current_os

    def _get_managed_blender_executable_full_path(self, folder_name):
        """
        Determines the full absolute path to the blender executable within an extracted Blender folder.
        'folder_name' is like 'blender-X.Y.Z-platform'.
        Returns the absolute path to the executable (e.g., 'C:/.../blender.exe') or None if not found.
        """
        if not config.CURRENT_PLATFORM_BLENDER_DETAILS:
            logger.error(
                f"Cannot determine executable path for unsupported OS: {platform.system()} {platform.machine().lower()}")  # <-- Changed print to logger.error
            return None

        base_path = os.path.join(config.MANAGED_TOOLS_DIR, 'blender', folder_name)

        expected_exe_subpath = config.CURRENT_PLATFORM_BLENDER_DETAILS['executable_path_in_folder']
        full_exe_path = os.path.join(base_path, expected_exe_subpath)

        if os.path.exists(full_exe_path):
            return full_exe_path
        else:
            logger.error(
                f"Blender executable not found for managed folder '{folder_name}' at expected path: {full_exe_path}. Double check platform mapping or extraction.")  # <-- Changed print to logger.error
            blender_exe_win = os.path.join(base_path, 'blender.exe')
            blender_exe_linux = os.path.join(base_path, 'blender')
            blender_exe_mac = os.path.join(base_path, 'blender.app', 'Contents', 'MacOS', 'blender')

            if os.path.exists(blender_exe_win) or os.path.exists(blender_exe_linux) or os.path.exists(blender_exe_mac):
                logger.warning(
                    f"Executable for managed folder '{folder_name}' found, but not at expected path for {platform.system()} {platform.machine().lower()}. Expected: {expected_exe_subpath}. Found: Windows={os.path.exists(blender_exe_win)}, Linux={os.path.exists(blender_exe_linux)}, MacOS={os.path.exists(blender_exe_mac)}")  # <-- Changed print to logger.warning
            return None

    def get_blender_executable_path(self, version_string):
        """
        Constructs the absolute path to the blender executable for a managed version based on current OS/Arch.
        This method is called externally (e.g., by job_processor).
        """
        if not config.CURRENT_PLATFORM_BLENDER_DETAILS:
            logger.error(
                f"Cannot get Blender executable path for unsupported OS: {platform.system()} {platform.machine().lower()}")  # <-- Changed print to logger.error
            return None

        platform_suffix = config.CURRENT_PLATFORM_BLENDER_DETAILS['download_suffix']
        blender_folder_name = f"blender-{version_string}-{platform_suffix}"

        return self._get_managed_blender_executable_full_path(blender_folder_name)

    def ensure_blender_version_available(self, required_version):
        """
        Checks if a Blender version is available locally. If not, downloads and extracts it.
        Uses the generated local JSON info for download URLs, trying mirrors if primary fails.
        Returns the path to the executable if successful, None otherwise.
        """
        logger.info(
            f"Checking for Blender version {required_version} availability.")  # <-- Changed print to logger.info

        if not config.CURRENT_PLATFORM_BLENDER_DETAILS:
            logger.error(
                f"Cannot manage Blender versions for unsupported OS: {platform.system()} {platform.machine().lower()}")  # <-- Changed print to logger.error
            return None

        # First, check if the required version is already present and correctly identified by scanner
        available_tools = self.scan_for_blender_versions()
        if 'blender' in available_tools and required_version in available_tools['blender']:
            logger.info(
                f"Blender version {required_version} already available locally. Path: {self.get_blender_executable_path(required_version)}")  # <-- Changed print to logger.info
            return self.get_blender_executable_path(required_version)

        # If not available locally, try to discover and download it using generated info
        # This returns the filtered dict for the CURRENT OS, keyed by version.
        blender_download_info = self.generate_and_cache_blender_download_info()
        version_entry_for_current_os = blender_download_info.get(required_version)

        if version_entry_for_current_os:  # Found the specific entry for this version and OS
            primary_url = version_entry_for_current_os.get('url')
            mirrors = version_entry_for_current_os.get('mirrors', [])
            expected_hash = version_entry_for_current_os.get('hash')
            download_ext = version_entry_for_current_os.get('file_extension')
            platform_suffix_from_entry = version_entry_for_current_os.get('platform_suffix')

            if not download_ext:
                logger.error(
                    f"Missing download extension for {required_version} in cache. Cannot download.")  # <-- Changed print to logger.error
                return None

            download_urls_to_try = [primary_url] + mirrors

            downloaded_successfully = False
            final_download_url = None
            for url_to_try in download_urls_to_try:
                if not url_to_try:
                    continue

                # Construct temp_file_name based on version and platform from the entry
                temp_file_name = f"blender-{required_version}-{platform_suffix_from_entry}{download_ext}"
                temp_file_path = os.path.join(config.MANAGED_TOOLS_DIR, 'blender', temp_file_name)
                extract_to_path = os.path.join(config.MANAGED_TOOLS_DIR, 'blender')

                os.makedirs(extract_to_path, exist_ok=True)

                if download_file(url=url_to_try, destination_path=temp_file_path, expected_hash=expected_hash):
                    downloaded_successfully = True
                    final_download_url = url_to_try
                    break
                else:
                    logger.warning(
                        f"Failed to download from primary/mirror URL: {url_to_try}. Trying next...")  # <-- Changed print to logger.warning

            if downloaded_successfully:
                success, actual_extract_path = extract_zip_file(zip_path=temp_file_path,
                                                                extract_to_path=extract_to_path)
                if success:
                    os.remove(temp_file_path)
                    logger.info(
                        f"Cleaned up temporary download file: {temp_file_path}")  # <-- Changed print to logger.info
                    return self.get_blender_executable_path(required_version)
                else:
                    logger.error(
                        f"FAILED to extract Blender {required_version} from {final_download_url}.")  # <-- Changed print to logger.error
                    return None
            else:
                logger.error(
                    f"Failed to download Blender {required_version} for {platform.system()} {platform.machine().lower()} from any available URL.")  # <-- Changed print to logger.error
                return None
        else:  # No entry found for the required_version and current_platform_suffix
            logger.error(
                f"Requested Blender version {required_version} for {platform.system()} {platform.machine().lower()} not found in generated download info (blender_versions_cache.json) or is not 4.x+.")  # <-- Changed print to logger.error
            return None


# --- Create a single instance of ToolManager for other modules to import and use ---
tool_manager_instance = ToolManager()