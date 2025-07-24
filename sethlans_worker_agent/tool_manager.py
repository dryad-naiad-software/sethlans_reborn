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

import json
import logging
import os
import platform
import re

from . import config
from .utils.blender_release_parser import fetch_page_soup, parse_major_version_directories, \
    collect_blender_version_details
from .utils.file_operations import download_file, extract_zip_file

logger = logging.getLogger(__name__)


# Class to encapsulate tool management logic and its cache
class ToolManager:
    _instance = None  # Singleton instance to ensure only one ToolManager object

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ToolManager, cls).__new__(cls)
            # No initialization here in __new__, only instance creation
        return cls._instance

    def __init__(self):
        # Ensure initialization only happens once for the singleton instance
        if not hasattr(self, '_initialized'):
            self.CACHED_BLENDER_DOWNLOAD_INFO = []
            logger.info("Initializing ToolManager instance.")
            self.is_supported_platform = config.CURRENT_PLATFORM_BLENDER_DETAILS is not None
            if not self.is_supported_platform:
                logger.critical(
                    f"ToolManager initialized on an unsupported platform: {platform.system()} {platform.machine().lower()}. Most functionality will be disabled.")
            self._initialized = True  # Set flag after first initialization

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
        if not current_os_download_suffix:
            logger.warning("Cannot scan for Blender: platform details not configured for this OS.")
            return {}

        if not os.path.isdir(blender_path):
            logger.info(f"Blender tools directory does not exist, nothing to scan: {blender_path}")
            return {'blender': []}

        logger.debug(f"Scanning for local Blender versions in: {blender_path}")
        for item_name in os.listdir(blender_path):
            full_path = os.path.join(blender_path, item_name)

            if not os.path.isdir(full_path):
                logger.debug(f"Skipping item '{item_name}': Not a directory.")
                continue

            version_platform_match = re.search(r'blender-(\d+\.\d+\.\d+)-(.+)', item_name)
            if not version_platform_match:
                logger.warning(f"Could not extract version/platform from folder name '{item_name}'. Skipping.")
                continue

            version_str = version_platform_match.group(1)
            platform_suffix_found = version_platform_match.group(2)

            if platform_suffix_found != current_os_download_suffix:
                logger.debug(
                    f"Skipping folder '{item_name}': Platform '{platform_suffix_found}' doesn't match current OS.")
                continue

            # This call itself contains checks for executable path existence
            if not self._get_managed_blender_executable_full_path(item_name):
                logger.warning(f"Skipping folder '{item_name}': No recognized blender executable found.")
                continue

            try:
                major_version = int(version_str.split('.')[0])
                if major_version < 4:
                    logger.debug(f"Skipping managed Blender version < 4.x: {version_str}")
                    continue
            except (ValueError, IndexError):
                logger.warning(f"Could not parse major version from '{version_str}'. Skipping.")
                continue

            # If we passed all checks, it's a valid version
            logger.info(f"  Found managed Blender version: {version_str} for {platform_suffix_found}")
            blender_versions_found.append(version_str)

        blender_versions_found.sort()

        if not blender_versions_found:
            logger.info(
                f"No Blender versions 4.x+ found for {platform.system()} {platform.machine().lower()} in {blender_path}.")

        return {'blender': blender_versions_found}

    def _load_blender_cache(self):
        """Helper to load Blender download info from the local JSON cache file."""
        if os.path.exists(config.BLENDER_VERSIONS_CACHE_FILE):
            try:
                with open(config.BLENDER_VERSIONS_CACHE_FILE, 'r') as f:
                    loaded_data_list = json.load(f)
                    self.CACHED_BLENDER_DOWNLOAD_INFO = loaded_data_list
                    logger.info(f"Loaded Blender download info from local cache: {config.BLENDER_VERSIONS_CACHE_FILE}.")
                    return True
            except json.JSONDecodeError:
                logger.warning(f"Blender versions cache file corrupted. Will re-generate.")
                self.CACHED_BLENDER_DOWNLOAD_INFO = []
            except Exception as e:
                logger.warning(f"Failed to load Blender versions cache: {e}. Will re-generate.")
        return False

    def _save_blender_cache(self, data_list):
        """Helper to save Blender download info to the local JSON cache file."""
        try:
            os.makedirs(os.path.dirname(config.BLENDER_VERSIONS_CACHE_FILE), exist_ok=True)
            with open(config.BLENDER_VERSIONS_CACHE_FILE, 'w') as f:
                json.dump(data_list, f, indent=4)
            logger.info(f"Saved generated Blender download info to local cache: {config.BLENDER_VERSIONS_CACHE_FILE}.")
            return True
        except Exception as e:
            logger.error(f"Failed to save Blender versions cache: {e}")
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
                        f"  Selected latest for {major_minor_key} series and platform '{platform_suffix_key}': {latest_version_for_platform['version']}")
                else:
                    logger.debug(
                        f"No versions found for {major_minor_key} series and platform '{platform_suffix_key}'.")

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
            logger.info(f"Using in-memory cache for Blender download info (loaded from file).")

            current_platform_suffix = config.CURRENT_PLATFORM_BLENDER_DETAILS.get(
                'download_suffix') if config.CURRENT_PLATFORM_BLENDER_DETAILS else None
            if not current_platform_suffix:
                logger.error(
                    f"Cannot filter cache for unsupported OS: {platform.system()} {platform.machine().lower()}.")
                return {}

            filtered_cache_for_current_os = {}
            for entry in self.CACHED_BLENDER_DOWNLOAD_INFO:
                if entry.get('platform_suffix') == current_platform_suffix:
                    filtered_cache_for_current_os[entry['version']] = entry
            self.CACHED_BLENDER_DOWNLOAD_INFO = filtered_cache_for_current_os
            return self.CACHED_BLENDER_DOWNLOAD_INFO

        logger.info(f"Performing dynamic Blender download info generation (4.x+ only) with mirrors...")

        versions_by_major_minor = {}

        try:
            main_soup = fetch_page_soup(config.BLENDER_RELEASES_URL)
            if not main_soup:
                logger.error(f"Could not fetch main Blender releases page.")
                return {}

            major_version_dir_urls = parse_major_version_directories(main_soup)

            for major_version_dir_url_blender_org in major_version_dir_urls:
                versions_from_dir = collect_blender_version_details(
                    major_version_dir_url_blender_org)

                for version_data in versions_from_dir:
                    major_minor_key = ".".join(version_data['version'].split('.')[:2])
                    if major_minor_key not in versions_by_major_minor:
                        versions_by_major_minor[major_minor_key] = []
                    versions_by_major_minor[major_minor_key].append(version_data)

                if not versions_from_dir:
                    logger.info(
                        f"No 4.x+ zip/tar.xz/dmg files found in {major_version_dir_url_blender_org}")

        except Exception as e:
            logger.error(f"An unexpected error occurred during URL discovery/generation: {e}")
            return {}

        generated_info_list_all_platforms = self._filter_and_process_major_minor_versions(versions_by_major_minor)

        self._save_blender_cache(generated_info_list_all_platforms)

        current_platform_suffix = config.CURRENT_PLATFORM_BLENDER_DETAILS.get(
            'download_suffix') if config.CURRENT_PLATFORM_BLENDER_DETAILS else None

        if not current_platform_suffix:
            logger.error(
                f"Cannot filter generated info for unsupported OS: {platform.system()} {platform.machine().lower()}.")
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
        This function strictly checks only the executable path for the CURRENT_PLATFORM_BLENDER_DETAILS.
        """
        if not config.CURRENT_PLATFORM_BLENDER_DETAILS:
            logger.error(
                f"Cannot determine executable path for unsupported OS: {platform.system()} {platform.machine().lower()}")
            return None

        base_path = os.path.join(config.MANAGED_TOOLS_DIR, 'blender', folder_name)

        expected_exe_subpath = config.CURRENT_PLATFORM_BLENDER_DETAILS['executable_path_in_folder']
        full_exe_path = os.path.join(base_path, expected_exe_subpath)

        if os.path.exists(full_exe_path):
            return full_exe_path
        else:
            logger.error(
                f"Blender executable not found for managed folder '{folder_name}' at expected path: {full_exe_path}. Double check platform mapping or extraction.")
            return None

    def get_blender_executable_path(self, version_string):
        """
        Constructs the absolute path to the blender executable for a managed version based on current OS/Arch.
        This method is called externally (e.g., by job_processor).
        """
        if not config.CURRENT_PLATFORM_BLENDER_DETAILS:
            logger.error(
                f"Cannot get Blender executable path for unsupported OS: {platform.system()} {platform.machine().lower()}")
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
        logger.info(f"Checking for Blender version {required_version} availability.")

        if not config.CURRENT_PLATFORM_BLENDER_DETAILS:
            logger.error(
                f"Cannot manage Blender versions for unsupported OS: {platform.system()} {platform.machine().lower()}.")
            return None

        # First, check if the required version is already present and correctly identified by scanner
        available_tools = self.scan_for_blender_versions()
        if 'blender' in available_tools and required_version in available_tools['blender']:
            logger.info(
                f"Blender version {required_version} already available locally. Path: {self.get_blender_executable_path(required_version)}")
            return self.get_blender_executable_path(required_version)

        # If not available locally, try to discover and download it using generated info
        # This returns the filtered dict for the CURRENT OS, keyed by version.
        blender_download_info = self.generate_and_cache_blender_download_info()
        version_entry_for_current_os = blender_download_info.get(required_version)

        if version_entry_for_current_os:
            primary_url = version_entry_for_current_os.get('url')
            mirrors = version_entry_for_current_os.get('mirrors', [])
            expected_hash = version_entry_for_current_os.get('hash')
            download_ext = version_entry_for_current_os.get('file_extension')
            platform_suffix_from_entry = version_entry_for_current_os.get('platform_suffix')

            if not download_ext:
                logger.error(f"Missing download extension for {required_version} in cache. Cannot download.")
                return None

            download_urls_to_try = [primary_url] + mirrors

            downloaded_successfully = False
            final_download_url = None
            for url_to_try in download_urls_to_try:
                if not url_to_try:
                    continue

                temp_file_name = f"blender-{required_version}-{platform_suffix_from_entry}{download_ext}"
                temp_file_path = os.path.join(config.MANAGED_TOOLS_DIR, 'blender', temp_file_name)
                extract_to_path = os.path.join(config.MANAGED_TOOLS_DIR, 'blender')

                os.makedirs(extract_to_path, exist_ok=True)

                if download_file(url=url_to_try, destination_path=temp_file_path, expected_hash=expected_hash):
                    downloaded_successfully = True
                    final_download_url = url_to_try
                    break
                else:
                    logger.warning(f"Failed to download from primary/mirror URL: {url_to_try}. Trying next...")

            if downloaded_successfully:
                # This is the standardized folder name we expect for all our logic
                expected_folder_name = f"blender-{required_version}-{platform_suffix_from_entry}"
                expected_final_path = os.path.join(extract_to_path, expected_folder_name)

                success, actual_extract_path = extract_zip_file(zip_path=temp_file_path,
                                                                extract_to_path=extract_to_path)
                if success:
                    # Clean up the downloaded archive first
                    os.remove(temp_file_path)
                    logger.info(f"Cleaned up temporary download file: {temp_file_path}")

                    # --- This is the new robust part ---
                    # If the extracted folder name is not what we expect, rename it.
                    if actual_extract_path and actual_extract_path != expected_final_path:
                        logger.info(
                            f"Standardizing extracted folder name from '{os.path.basename(actual_extract_path)}' to '{expected_folder_name}'.")
                        try:
                            os.rename(actual_extract_path, expected_final_path)
                        except OSError as e:
                            logger.error(f"Failed to rename extracted folder: {e}")
                            return None  # Can't proceed if rename fails

                    # Now we can be certain the executable path will be found
                    executable_path = self.get_blender_executable_path(required_version)
                    if executable_path:
                        logger.info(
                            f"Successfully downloaded and extracted Blender {required_version}. Path: {executable_path}")
                        return executable_path
                    else:
                        # This case would indicate a problem with the config or archive contents
                        logger.error(
                            f"Extraction seemed successful, but could not find the executable for {required_version}.")
                        return None
                else:
                    logger.error(f"FAILED to extract Blender {required_version} from {final_download_url}.")
                    return None
            else:
                logger.error(
                    f"Failed to download Blender {required_version} for {platform.system()} {platform.machine().lower()} from any available URL.")
                return None
        else:
            logger.error(
                f"Requested Blender version {required_version} for {platform.system()} {platform.machine().lower()} not found in generated download info (blender_versions_cache.json) or is not 4.x+.")
            return None


# --- Create a single instance of ToolManager for other modules to import and use ---
tool_manager_instance = ToolManager()
