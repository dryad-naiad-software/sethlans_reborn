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


from utils.blender_release_parser import fetch_page_soup, parse_major_version_directories, \
    collect_blender_version_details
from . import config
from .utils.file_operations import download_file, extract_zip_file


# Class to encapsulate tool management logic and its cache
class ToolManager:
    _instance = None  # Singleton instance to ensure only one ToolManager object

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ToolManager, cls).__new__(cls)
            cls._instance.CACHED_BLENDER_DOWNLOAD_INFO = {}  # Initialize cache on the instance
            print(f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Initializing ToolManager instance.")
        return cls._instance

    def scan_for_blender_versions(self):
        """
        Scans the managed_tools directory for installed Blender versions, filtering for 4.x and greater.
        Returns a dictionary like {'blender': ['4.0.0', '4.1.0']}
        """
        blender_versions_found = []
        blender_path = os.path.join(config.MANAGED_TOOLS_DIR, 'blender')

        if os.path.exists(blender_path) and os.path.isdir(blender_path):
            print(
                f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Scanning for local Blender versions in: {blender_path}")
            for item_name in os.listdir(blender_path):
                full_path = os.path.join(blender_path, item_name)

                if os.path.isdir(full_path):
                    blender_exe_path_win = os.path.join(full_path, 'blender.exe')
                    blender_exe_path_linux = os.path.join(full_path, 'blender')

                    if os.path.exists(blender_exe_path_win) or os.path.exists(blender_exe_path_linux):
                        version_match = re.search(r'blender-(\d+\.\d+\.\d+)', item_name)
                        if version_match:
                            version_str = version_match.group(1)
                            try:
                                major_version = int(version_str.split('.')[0])
                                if major_version >= 4:
                                    blender_versions_found.append(version_str)
                                    print(
                                        f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}]   Found managed Blender version: {version_str} at {full_path}")
                                else:  # <-- CORRECTED INDENTATION
                                    print(
                                        f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Skipping managed Blender version < 4.x: {version_str}")
                            except ValueError:  # <-- CORRECTED INDENTATION
                                print(
                                    f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Warning: Could not parse major version from '{version_str}'. Skipping.")
                        else:  # <-- CORRECTED INDENTATION
                            print(
                                f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Warning: Could not extract version from folder name '{item_name}'. Skipping.")
                else:  # <-- CORRECTED INDENTATION
                    print(
                        f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Skipping folder '{item_name}': No blender executable found inside.")

        blender_versions_found.sort()

        available_tools = {}
        if blender_versions_found:
            available_tools['blender'] = blender_versions_found
        else:
            print(
                f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] No Blender versions 4.x+ found in {blender_path}.")

        return available_tools  # <-- CORRECTED INDENTATION

    def _load_blender_cache(self):
        """Helper to load Blender download info from the local JSON cache file."""
        if os.path.exists(config.BLENDER_VERSIONS_CACHE_FILE):
            try:
                with open(config.BLENDER_VERSIONS_CACHE_FILE, 'r') as f:
                    loaded_data_list = json.load(f)
                    self.CACHED_BLENDER_DOWNLOAD_INFO = {entry['version']: entry for entry in loaded_data_list if
                                                         'version' in entry}
                    print(
                        f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Loaded Blender download info from local cache: {config.BLENDER_VERSIONS_CACHE_FILE}.")
                    return True
            except json.JSONDecodeError:
                print(
                    f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Warning: Blender versions cache file corrupted. Will re-generate.")
            except Exception as e:
                print(
                    f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Warning: Failed to load Blender versions cache: {e}. Will re-generate.")
        return False

    def _save_blender_cache(self, data_dict):
        """Helper to save Blender download info to the local JSON cache file."""
        try:
            os.makedirs(os.path.dirname(config.BLENDER_VERSIONS_CACHE_FILE), exist_ok=True)
            json_output_list = list(data_dict.values())
            json.dump(json_output_list, open(config.BLENDER_VERSIONS_CACHE_FILE, 'w'), indent=4)
            print(
                f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Saved generated Blender download info to local cache: {config.BLENDER_VERSIONS_CACHE_FILE}.")
            return True
        except Exception as e:
            print(
                f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: Failed to save Blender versions cache: {e}")
            return False

    def _filter_and_process_major_minor_versions(self, versions_by_major_minor_raw):
        """
        Takes raw collected version data and filters it down to the latest patch for each major.minor series.
        Returns a dictionary keyed by full version string.
        """
        final_filtered_info = {}
        for major_minor_key, versions_list in versions_by_major_minor_raw.items():
            versions_list.sort(key=lambda x: [int(v) for v in x['version'].split('.')], reverse=True)
            if versions_list:
                latest_version_overall = versions_list[0]
                final_filtered_info[latest_version_overall['version']] = latest_version_overall
                print(
                    f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}]   Selected latest for {major_minor_key} series: {latest_version_overall['version']}")
        return final_filtered_info

    def generate_and_cache_blender_download_info(self):
        """
        Orchestrates the generation of comprehensive Blender download info.
        Tries to load from cache, otherwise performs dynamic discovery, filters, and saves.
        Returns a dictionary mapping version string to its download info dictionary.
        """
        # Try to load from cache file first
        if self._load_blender_cache():
            print(
                f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Using in-memory cache for Blender download info (loaded from file).")
            return self.CACHED_BLENDER_DOWNLOAD_INFO

        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Performing dynamic Blender download info generation (4.x+ only) with mirrors...")

        versions_by_major_minor = {}  # Key: "Major.Minor" (e.g., "4.1"), Value: List of version info dicts for that series
        generated_info = {}  # Initialize this here, it will hold the final filtered info

        try:
            main_soup = fetch_page_soup(config.BLENDER_RELEASES_URL)
            if not main_soup:
                return {}

            major_version_dir_urls = parse_major_version_directories(main_soup)

            for major_version_dir_url_blender_org in major_version_dir_urls:
                versions_in_dir_windows_x64_zip = collect_blender_version_details(major_version_dir_url_blender_org)

                for version_data in versions_in_dir_windows_x64_zip:
                    major_minor_key = ".".join(version_data['version'].split('.')[:2])
                    if major_minor_key not in versions_by_major_minor:
                        versions_by_major_minor[major_minor_key] = []
                    versions_by_major_minor[major_minor_key].append(version_data)

                if not versions_in_dir_windows_x64_zip:
                    print(
                        f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] No 4.x+ Windows x64 zip files found in {major_version_dir_url_blender_org}")

        except Exception as e:
            print(
                f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] An unexpected error occurred during URL discovery/generation: {e}")

        generated_info = self._filter_and_process_major_minor_versions(versions_by_major_minor)

        self._save_blender_cache(generated_info)

        self.CACHED_BLENDER_DOWNLOAD_INFO = generated_info
        return generated_info

    def get_blender_executable_path(self, version_string):
        """
        Constructs the absolute path to the blender.exe for a managed version.
        Assumes naming convention like 'blender-X.Y.Z-windows-x64' inside managed_tools/blender/
        """
        blender_folder_name = f"blender-{version_string}-windows-x64"

        blender_version_path = os.path.join(config.MANAGED_TOOLS_DIR, 'blender', blender_folder_name)
        blender_exe_path = os.path.join(blender_version_path, 'blender.exe')  # For Windows

        if not os.path.exists(blender_exe_path):
            blender_exe_path_linux = os.path.join(blender_version_path, 'blender')
            if os.path.exists(blender_exe_path_linux):
                blender_exe_path = blender_exe_path_linux
            else:
                print(
                    f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: Blender executable not found for version {version_string} at expected path: {blender_version_path}. No blender.exe or blender found.")
                return None

        return blender_exe_path

    def ensure_blender_version_available(self, required_version):
        """
        Checks if a Blender version is available locally. If not, downloads and extracts it.
        Uses the generated local JSON info for download URLs, trying mirrors if primary fails.
        Returns the path to the executable if successful, None otherwise.
        """
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Checking for Blender version {required_version} availability.")
        # First, check if the required version is already present and correctly identified by scanner
        available_tools = self.scan_for_blender_versions()
        if 'blender' in available_tools and required_version in available_tools['blender']:
            print(
                f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Blender version {required_version} already available locally. Path: {self.get_blender_executable_path(required_version)}")
            return self.get_blender_executable_path(required_version)

        # If not available locally, try to discover and download it using generated info
        blender_download_info = self.generate_and_cache_blender_download_info()
        version_entry = blender_download_info.get(required_version)

        if version_entry:
            primary_url = version_entry.get('url')
            mirrors = version_entry.get('mirrors', [])
            expected_hash = version_entry.get('hash')

            download_urls_to_try = [primary_url] + mirrors

            downloaded_successfully = False
            final_download_url = None
            for url_to_try in download_urls_to_try:
                if not url_to_try:
                    continue

                zip_filename_match = re.search(r'blender-(\d+\.\d+\.\d+)-windows-x64\.zip$', url_to_try)
                if not zip_filename_match:
                    print(
                        f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: Could not determine zip filename from URL: {url_to_try}. Skipping this URL.")
                    continue
                temp_zip_filename = zip_filename_match.group(0)

                temp_zip_path = os.path.join(config.MANAGED_TOOLS_DIR, 'blender', temp_zip_filename)
                extract_to_path = os.path.join(config.MANAGED_TOOLS_DIR, 'blender')

                os.makedirs(extract_to_path, exist_ok=True)

                if download_file(url=url_to_try, destination_path=temp_zip_path, expected_hash=expected_hash):
                    downloaded_successfully = True
                    final_download_url = url_to_try
                    break
                else:
                    print(
                        f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Failed to download from primary/mirror URL: {url_to_try}. Trying next...")

            if downloaded_successfully:
                success, actual_extract_path = extract_zip_file(zip_path=temp_zip_path, extract_to_path=extract_to_path)
                if success:
                    os.remove(temp_zip_path)
                    print(
                        f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Cleaned up temporary zip file: {temp_zip_path}")
                    return self.get_blender_executable_path(required_version)
                else:
                    print(
                        f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] FAILED to extract Blender {required_version} from {final_download_url}.")
                    return None
            else:
                print(
                    f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: Failed to download Blender {required_version} from any available URL.")
                return None
        else:
            print(
                f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: Requested Blender version {required_version} not found in generated download info (blenderdownloads.json) or is not 4.x+.")
            return None


# --- Create a single instance of ToolManager for other modules to import and use ---
tool_manager_instance = ToolManager()