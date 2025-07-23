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
import zipfile
import shutil
import datetime

from bs4 import BeautifulSoup  # Required for HTML parsing
from urllib.parse import urljoin  # Required for constructing URLs

from . import config  # Import config module for settings

# Cache for loaded Blender download info
CACHED_BLENDER_DOWNLOAD_INFO = {}


# --- Tool Management Functions ---

def scan_for_blender_versions():
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
                # Check for blender.exe (Windows) or blender (Linux) inside the version folder
                blender_exe_path_win = os.path.join(full_path, 'blender.exe')
                blender_exe_path_linux = os.path.join(full_path, 'blender')

                if os.path.exists(blender_exe_path_win) or os.path.exists(blender_exe_path_linux):
                    # Extract version string from folder name (e.g., "4.0.0" from "blender-4.0.0-windows-x64")
                    version_match = re.search(r'blender-(\d+\.\d+\.\d+)', item_name)
                    if version_match:
                        version_str = version_match.group(1)
                        # Filter for 4.x and greater
                        try:
                            major_version = int(version_str.split('.')[0])
                            if major_version >= 4:
                                blender_versions_found.append(version_str)
                                print(
                                    f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}]   Found managed Blender version: {version_str} at {full_path}")
                            else:
                                print(
                                    f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Skipping managed Blender version < 4.x: {version_str}")
                        except ValueError:
                            print(
                                f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Warning: Could not parse major version from '{version_str}'. Skipping.")
                    else:
                        print(
                            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Warning: Could not extract version from folder name '{item_name}'. Skipping.")
                else:
                    print(
                        f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Skipping folder '{item_name}': No blender executable found inside.")

    blender_versions_found.sort()

    available_tools = {}
    if blender_versions_found:
        available_tools['blender'] = blender_versions_found
    else:
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] No Blender versions 4.x+ found in {blender_path}.")

    return available_tools


def generate_and_cache_blender_download_info():
    """
    Generates a comprehensive list of Blender download info (primary URL + mirrors),
    filters for 4.x+, and caches it to BLENDER_VERSIONS_CACHE_FILE.
    Returns a dictionary mapping version string to its download info dictionary.
    """
    global CACHED_BLENDER_DOWNLOAD_INFO

    # Try to load from cache file first
    if os.path.exists(config.BLENDER_VERSIONS_CACHE_FILE):
        try:
            with open(config.BLENDER_VERSIONS_CACHE_FILE, 'r') as f:
                # Cache stores a list, convert it to dict for easier lookup by version
                loaded_data = json.load(f)
                CACHED_BLENDER_DOWNLOAD_INFO = {entry['version']: entry for entry in loaded_data if 'version' in entry}
                print(
                    f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Loaded Blender download info from local cache: {config.BLENDER_VERSIONS_CACHE_FILE}.")
                return CACHED_BLENDER_DOWNLOAD_INFO
        except json.JSONDecodeError:
            print(
                f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Warning: Blender versions cache file corrupted. Re-generating.")
            CACHED_BLENDER_DOWNLOAD_INFO = {}
        except Exception as e:
            print(
                f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Warning: Failed to load Blender versions cache: {e}. Re-generating.")
            CACHED_BLENDER_DOWNLOAD_INFO = {}

    # If cache is empty or failed to load, perform dynamic discovery and generate comprehensive info
    print(
        f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Performing dynamic Blender download info generation (4.x+ only) with mirrors...")
    generated_info = {}

    try:
        # Step 1: Get the main release page from blender.org
        response = requests.get(config.BLENDER_RELEASES_URL, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        for link in soup.find_all('a', href=True):
            href = link['href']
            major_version_dir_match = re.match(r'Blender(\d+\.\d+(?:\.\d+)?)/$', href)
            if major_version_dir_match:
                major_version_str = major_version_dir_match.group(1).split('.')[0]
                try:
                    major_version_num = int(major_version_str)
                    if major_version_num < 4:
                        print(
                            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Skipping Blender major version < 4: {major_version_dir_match.group(1)}")
                        continue
                except ValueError:
                    print(
                        f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Warning: Could not parse major version from directory '{href}'. Skipping.")
                    continue

                major_version_dir_url_blender_org = urljoin(config.BLENDER_RELEASES_URL, href)

                # Step 2: Dive into each major version directory on blender.org
                dir_response = requests.get(major_version_dir_url_blender_org, timeout=10)
                dir_response.raise_for_status()
                dir_soup = BeautifulSoup(dir_response.text, 'html.parser')

                for file_link in dir_soup.find_all('a', href=True):
                    file_href = file_link['href']
                    blender_zip_match = re.match(r'blender-(\d+\.\d+\.\d+)-windows-x64\.zip$', file_href)
                    if blender_zip_match:
                        full_version = blender_zip_match.group(1)
                        try:
                            file_major_version = int(full_version.split('.')[0])
                            if file_major_version < 4:
                                print(
                                    f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Skipping zip file Blender version < 4.x: {full_version}")
                                continue
                        except ValueError:
                            print(
                                f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Warning: Could not parse major version from zip file '{full_version}'. Skipping.")
                            continue

                        primary_download_url = urljoin(major_version_dir_url_blender_org, file_href)

                        # Construct mirror URLs
                        mirrors_for_version = []
                        for mirror_base in config.BLENDER_MIRROR_BASE_URLS:
                            # Replace the blender.org base with the mirror base
                            mirror_url = primary_download_url.replace(config.BLENDER_RELEASES_URL, mirror_base)
                            mirrors_for_version.append(mirror_url)

                        generated_info[full_version] = {
                            "releaseName": f"Blender {full_version}",
                            "version": full_version,
                            "hash": None,  # We don't compute hash here, but could be added later
                            "url": primary_download_url,
                            "mirrors": mirrors_for_version
                        }
                        print(
                            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}]   Generated info for {full_version}: Primary: {primary_download_url}, Mirrors: {mirrors_for_version}")

    except requests.exceptions.RequestException as e:
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: Failed to fetch Blender download page: {e}")
    except Exception as e:
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] An unexpected error occurred during URL discovery/generation: {e}")

    # Cache the results to a file
    try:
        os.makedirs(os.path.dirname(config.BLENDER_VERSIONS_CACHE_FILE), exist_ok=True)
        json_output_list = list(generated_info.values())
        json.dump(json_output_list, open(config.BLENDER_VERSIONS_CACHE_FILE, 'w'), indent=4)
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Saved generated Blender download info to local cache: {config.BLENDER_VERSIONS_CACHE_FILE}")
    except Exception as e:
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: Failed to save Blender versions cache: {e}")

    CACHED_BLENDER_DOWNLOAD_INFO = generated_info
    return generated_info


def download_file(url, destination_path):
    """Downloads a file from a given URL to a destination path."""
    print(f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Downloading {url} to {destination_path}...")
    try:
        with requests.get(url, stream=True, timeout=300) as r:
            r.raise_for_status()
            with open(destination_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        print(f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Download complete.")
        return True
    except requests.exceptions.RequestException as e:
        print(f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: Download failed - {e}")
        return False
    except Exception as e:
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] An unexpected error occurred during download: {e}")
        return False


def extract_zip_file(zip_path, extract_to_path):
    """Extracts a ZIP file to a specified directory."""
    print(f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Extracting {zip_path} to {extract_to_path}...")
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            root_folder_name = ""
            for name in zip_ref.namelist():
                if '/' in name:
                    root_folder_name = name.split('/')[0]
                    break

            actual_extract_path = os.path.join(extract_to_path, root_folder_name)

            os.makedirs(actual_extract_path, exist_ok=True)
            zip_ref.extractall(extract_to_path)

        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Extraction complete to {actual_extract_path}.")
        return True, actual_extract_path
    except zipfile.BadZipFile:
        print(f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: Invalid ZIP file: {zip_path}")
        return False, None
    except Exception as e:
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: Failed to extract ZIP file {zip_path}: {e}")
        return False, None


def get_blender_executable_path(version_string):
    """
    Constructs the absolute path to the blender.exe for a managed version.
    Assumes naming convention like 'blender-X.Y.Z-windows-x64' inside managed_tools/blender/
    """
    blender_folder_name = f"blender-{version_string}-windows-x64"

    blender_version_path = os.path.join(config.MANAGED_TOOLS_DIR, 'blender', blender_folder_name)
    blender_exe_path = os.path.join(blender_version_path, 'blender.exe')

    if not os.path.exists(blender_exe_path):
        blender_exe_path_linux = os.path.join(blender_version_path, 'blender')
        if os.path.exists(blender_exe_path_linux):
            blender_exe_path = blender_exe_path_linux
        else:
            print(
                f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: Blender executable not found for version {version_string} at expected path: {blender_version_path}. No blender.exe or blender found.")
            return None

    return blender_exe_path


def ensure_blender_version_available(required_version):
    """
    Checks if a Blender version is available locally. If not, downloads and extracts it.
    Uses the generated local JSON info for download URLs, trying mirrors if primary fails.
    Returns the path to the executable if successful, None otherwise.
    """
    print(
        f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Checking for Blender version {required_version} availability.")
    # First, check if the required version is already present and correctly identified by scanner
    available_tools = scan_for_blender_versions()
    if 'blender' in available_tools and required_version in available_tools['blender']:
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Blender version {required_version} already available locally. Path: {get_blender_executable_path(required_version)}")
        return get_blender_executable_path(required_version)

    # If not available locally, try to discover and download it using generated info
    blender_download_info = generate_and_cache_blender_download_info()
    version_entry = blender_download_info.get(required_version)

    if version_entry:
        primary_url = version_entry.get('url')
        mirrors = version_entry.get('mirrors', [])

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

            if download_file(url=url_to_try, destination_path=temp_zip_path):
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
                return get_blender_executable_path(required_version)
            else:
                print(
                    f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Failed to extract Blender {required_version} from {final_download_url}.")
                return None
        else:
            print(
                f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: Failed to download Blender {required_version} from any available URL.")
            return None
    else:
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: Requested Blender version {required_version} not found in generated download info (blenderdownloads.json) or is not 4.x+.")
        return None