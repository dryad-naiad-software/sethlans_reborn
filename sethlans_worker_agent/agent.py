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

import requests
import json
import time
import platform
import socket
import os
import sys
import subprocess
import datetime
import re

from bs4 import BeautifulSoup
from urllib.parse import urljoin
import zipfile
import shutil

# --- Configuration ---
# Base URL of your Sethlans Reborn Manager's API endpoints
# Ensure your Django development server is running at this address!
MANAGER_API_URL = "http://127.0.0.1:8000/api/"

# Intervals for worker operations
HEARTBEAT_INTERVAL_SECONDS = 30  # How often the worker sends a heartbeat to update its 'last_seen'
JOB_POLLING_INTERVAL_SECONDS = 5  # How often the worker checks for new jobs

# Calculates the main project root from the worker agent's script location.
# agent.py is in sethlans_reborn/sethlans_worker_agent/, so its parent's parent is sethlans_reborn/
PROJECT_ROOT_FOR_WORKER = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), '..'))

# IMPORTANT: Path to your system's installed Blender executable for default rendering (if not managed)
BLENDER_EXECUTABLE = r"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe"

# Directory where Sethlans Reborn will manage downloaded/extracted tools (like Blender portable versions)
MANAGED_TOOLS_DIR = os.path.join(PROJECT_ROOT_FOR_WORKER, 'sethlans_worker_agent', 'managed_tools')

# Placeholder paths for testing blend files/output.
TEST_BLEND_FILE_PATH = os.path.join(PROJECT_ROOT_FOR_WORKER, 'test_scene.blend')
TEST_OUTPUT_DIR = os.path.join(PROJECT_ROOT_FOR_WORKER, 'render_test_output')

WORKER_INFO = {}

BLENDER_RELEASES_URL = "https://download.blender.org/release/"
# Explicit list of base URLs for mirrors
BLENDER_MIRROR_BASE_URLS = [
    "https://mirror.clarkson.edu/blender/release/",
    "http://ftp.halifax.rwth-aachen.de/blender/release/",
    "http://ftp.nluug.nl/pub/graphics/blender/release/",
]

# Cache for loaded Blender download info
CACHED_BLENDER_DOWNLOAD_INFO = {}

# Path to the local JSON cache file for Blender versions
BLENDER_VERSIONS_CACHE_FILE = os.path.join(MANAGED_TOOLS_DIR, 'blender_versions_cache.json')


def get_system_info():
    """
    Gathers basic system information and available managed tools for the heartbeat.
    """
    hostname = socket.gethostname()
    ip_address = None
    try:
        ip_address = socket.gethostbyname(hostname)
    except socket.gaierror:
        pass

    os_info = platform.system()
    if os_info == 'Windows':
        os_info += f" {platform.release()}"
    elif os_info == 'Linux':
        os_info += f" {platform.version()}"

    available_tools = scan_for_blender_versions()

    return {
        "hostname": hostname,
        "ip_address": ip_address,
        "os": os_info,
        "available_tools": available_tools
    }


def send_heartbeat(system_info):
    """
    Sends a heartbeat to the Django Manager's heartbeat API endpoint.
    Updates the global WORKER_INFO with the worker's ID received from the manager.
    """
    global WORKER_INFO
    heartbeat_url = f"{MANAGER_API_URL}heartbeat/"
    try:
        print(f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Sending heartbeat to {heartbeat_url}...")
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Heartbeat payload: {json.dumps(system_info, indent=2)}")

        response = requests.post(heartbeat_url, json=system_info, timeout=5)
        response.raise_for_status()

        response_data = response.json()
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Heartbeat successful: HTTP {response.status_code}")
        WORKER_INFO = response_data
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Worker registered as ID: {WORKER_INFO.get('id')}, Hostname: {WORKER_INFO.get('hostname')}")

    except requests.exceptions.Timeout:
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: Heartbeat timed out after 5 seconds.")
    except requests.exceptions.RequestException as e:
        print(f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: Heartbeat failed - {e}")
    except json.JSONDecodeError:
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: Failed to decode JSON response from heartbeat.")
    except Exception as e:
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] An unexpected error occurred during heartbeat: {e}")


def execute_blender_job(job_data):
    """
    Executes a Blender render job using subprocess.
    Returns (success: bool, stdout: str, stderr: str, error_message: str)
    """
    job_name = job_data.get('name', 'Unnamed Job')
    blend_file_path = job_data.get('blend_file_path')
    output_file_pattern = job_data.get('output_file_pattern')
    start_frame = job_data.get('start_frame', 1)
    end_frame = job_data.get('end_frame', 1)
    blender_version_req = job_data.get('blender_version')
    render_engine = job_data.get('render_engine', 'CYCLES')

    print(f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Starting render for job '{job_name}'...")
    print(f"  Blend File: {blend_file_path}")
    print(f"  Output Pattern: {output_file_pattern}")
    print(f"  Frames: {start_frame}-{end_frame}")
    print(f"  Engine: {render_engine}")
    if blender_version_req:
        print(f"  Requested Blender Version: {blender_version_req}")

    # --- Determine which Blender executable to use ---
    blender_to_use = BLENDER_EXECUTABLE  # Default to system-wide Blender
    if blender_version_req:
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Attempting to ensure Blender version {blender_version_req} is available...")  # <-- NEW DEBUG
        managed_blender_path = ensure_blender_version_available(blender_version_req)
        if managed_blender_path:
            blender_to_use = managed_blender_path
            print(
                f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Using managed Blender version from: {blender_to_use}")
        else:
            print(
                f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] WARNING: Requested Blender version {blender_version_req} not available/downloadable. Falling back to default system Blender: {blender_to_use}")
            # In a production environment, you might want to mark the job as ERROR if the specific version isn't found
            # and fallback is not desired.

    # Ensure output directory exists before rendering
    output_dir = os.path.dirname(output_file_pattern)
    if not os.path.exists(output_dir):
        try:
            os.makedirs(output_dir)
            print(
                f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Created output directory: {output_dir}")
        except OSError as e:
            err_msg = f"Failed to create output directory {output_dir}: {e}"
            print(f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: {err_msg}")
            return False, "", "", err_msg

    command = [
        blender_to_use,
        "-b",
        blend_file_path,
        "-o", output_file_pattern.replace('####', '#'),
        "-F", "PNG",
        "-E", render_engine,
    ]

    if start_frame == end_frame:
        command.extend(["-f", str(start_frame)])
    else:
        command.extend(["-s", str(start_frame), "-e", str(end_frame), "-a"])

    print(
        f"\n[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Running Blender command: {' '.join(command)}")

    stdout_output = ""
    stderr_output = ""
    error_message = ""
    success = False

    try:
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            cwd=PROJECT_ROOT_FOR_WORKER
        )

        stdout_output = process.stdout
        stderr_output = process.stderr

        if process.returncode == 0:
            print(
                f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Blender render command exited successfully.")
            success = True
        else:
            error_message = f"Blender exited with code {process.returncode}. STDERR: {stderr_output[:500]}..."
            print(
                f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: Blender command failed: {error_message}")
            success = False

        print("--- Blender STDOUT (last 1000 chars) ---")
        print(stdout_output[-1000:])
        print("--- Blender STDERR (last 1000 chars) ---")
        print(stderr_output[-1000:])

    except FileNotFoundError:
        error_message = f"Blender executable not found at '{blender_to_use}'. Please check the path/download."
        print(f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: {error_message}")
    except Exception as e:
        error_message = f"An unexpected error occurred during Blender execution: {e}"
        print(f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: {error_message}")

    return success, stdout_output, stderr_output, error_message


def scan_for_blender_versions():
    """
    Scans the managed_tools directory for installed Blender versions, filtering for 4.x and greater.
    Returns a dictionary like {'blender': ['4.0.0', '4.1.0']}
    """
    blender_versions_found = []
    blender_path = os.path.join(MANAGED_TOOLS_DIR, 'blender')

    if os.path.exists(blender_path) and os.path.isdir(blender_path):
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Scanning for local Blender versions in: {blender_path}")  # <-- NEW DEBUG
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
                                    f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}]   Found managed Blender version: {version_str} at {full_path}")  # <-- NEW DEBUG
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
                        f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Skipping folder '{item_name}': No blender executable found inside.")  # <-- NEW DEBUG

    blender_versions_found.sort()

    available_tools = {}
    if blender_versions_found:
        available_tools['blender'] = blender_versions_found
    else:
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] No Blender versions 4.x+ found in {blender_path}.")  # <-- NEW DEBUG

    return available_tools


def generate_and_cache_blender_download_info():
    """
    Generates a comprehensive list of Blender download info (primary URL + mirrors),
    filters for 4.x+, and caches it to BLENDER_VERSIONS_CACHE_FILE.
    Returns a dictionary mapping version string to its download info dictionary.
    """
    global CACHED_BLENDER_DOWNLOAD_INFO

    # Try to load from cache file first
    if os.path.exists(BLENDER_VERSIONS_CACHE_FILE):
        try:
            with open(BLENDER_VERSIONS_CACHE_FILE, 'r') as f:
                CACHED_BLENDER_DOWNLOAD_INFO = {entry['version']: entry for entry in
                                                json.load(f)}  # Convert list to dict
                print(
                    f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Loaded Blender download info from local cache: {BLENDER_VERSIONS_CACHE_FILE}.")  # <-- NEW DEBUG
                return CACHED_BLENDER_DOWNLOAD_INFO
        except json.JSONDecodeError:
            print(
                f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Warning: Blender versions cache file corrupted. Re-generating.")
            CACHED_BLENDER_DOWNLOAD_INFO = {}  # Clear cache if corrupted
        except Exception as e:
            print(
                f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Warning: Failed to load Blender versions cache: {e}. Re-generating.")
            CACHED_BLENDER_DOWNLOAD_INFO = {}

    # If cache is empty or failed to load, perform dynamic discovery and generate comprehensive info
    print(
        f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Performing dynamic Blender download info generation (4.x+ only) with mirrors...")  # <-- NEW DEBUG
    generated_info = {}

    try:
        # Step 1: Get the main release page from blender.org
        response = requests.get(BLENDER_RELEASES_URL, timeout=10)
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

                major_version_dir_url_blender_org = urljoin(BLENDER_RELEASES_URL, href)

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
                        for mirror_base in BLENDER_MIRROR_BASE_URLS:
                            # Replace the blender.org base with the mirror base
                            mirror_url = primary_download_url.replace(BLENDER_RELEASES_URL, mirror_base)
                            mirrors_for_version.append(mirror_url)

                        generated_info[full_version] = {
                            "releaseName": f"Blender {full_version}",
                            "version": full_version,
                            "hash": None,
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
        os.makedirs(os.path.dirname(BLENDER_VERSIONS_CACHE_FILE), exist_ok=True)
        json_output_list = list(generated_info.values())
        json.dump(json_output_list, open(BLENDER_VERSIONS_CACHE_FILE, 'w'), indent=4)
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Saved generated Blender download info to local cache: {BLENDER_VERSIONS_CACHE_FILE}")
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

    blender_version_path = os.path.join(MANAGED_TOOLS_DIR, 'blender', blender_folder_name)
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
        f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Checking for Blender version {required_version} availability.")  # <-- NEW DEBUG
    # First, check if the required version is already present and correctly identified by scanner
    available_tools = scan_for_blender_versions()
    if 'blender' in available_tools and required_version in available_tools['blender']:
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Blender version {required_version} already available locally. Path: {get_blender_executable_path(required_version)}")  # <-- NEW DEBUG
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

            temp_zip_path = os.path.join(MANAGED_TOOLS_DIR, 'blender', temp_zip_filename)
            extract_to_path = os.path.join(MANAGED_TOOLS_DIR, 'blender')

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


def get_and_claim_job():
    """Polls the manager for available jobs and attempts to claim one. If claimed, executes the job."""
    if not WORKER_INFO.get('id'):
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Worker ID not yet known. Skipping job poll.")
        return

    jobs_url = f"{MANAGER_API_URL}jobs/"
    try:
        print(f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Polling for jobs from {jobs_url}...")
        response = requests.get(jobs_url, params={'status': 'QUEUED', 'assigned_worker__isnull': 'true'}, timeout=10)
        response.raise_for_status()
        available_jobs = response.json()

        if available_jobs:
            print(
                f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Found {len(available_jobs)} available job(s).")
            job_to_claim = available_jobs[0]
            job_id = job_to_claim.get('id')
            job_name = job_to_claim.get('name')

            print(
                f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Attempting to claim job '{job_name}' (ID: {job_id})...")

            # Send a PATCH request to the manager to claim the job
            claim_url = f"{jobs_url}{job_id}/"
            claim_payload = {
                "status": "RENDERING",
                "assigned_worker": WORKER_INFO['id']
            }
            claim_response = requests.patch(claim_url, json=claim_payload, timeout=5)
            claim_response.raise_for_status()

            # Verify that the claim was successful from the manager's response
            claimed_job_data = claim_response.json()
            if claimed_job_data.get('status') == 'RENDERING' and claimed_job_data.get('assigned_worker') == WORKER_INFO[
                'id']:
                print(
                    f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Successfully claimed job '{job_name}'! Starting render...")

                # --- EXECUTE BLENDER JOB ---
                success, stdout, stderr, blender_error_msg = execute_blender_job(job_to_claim)

                # --- REPORT JOB STATUS BACK TO MANAGER ---
                job_update_payload = {
                    "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z'),
                    "last_output": stdout,
                    "error_message": blender_error_msg if blender_error_msg else stderr,
                }

                if success:
                    job_update_payload["status"] = "DONE"
                else:
                    job_update_payload["status"] = "ERROR"

                try:
                    print(
                        f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Reporting job '{job_name}' status '{job_update_payload['status']}' back to manager...")
                    report_response = requests.patch(claim_url, json=job_update_payload, timeout=5)
                    report_response.raise_for_status()
                    print(
                        f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Job '{job_name}' status report successful.")
                except requests.exceptions.RequestException as e:
                    print(
                        f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: Failed to report job status for '{job_name}' - {e}")

                return job_to_claim
            else:
                print(
                    f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: Claim failed or manager response was unexpected. Job status: {claimed_job_data.get('status')}. Worker assigned: {claimed_job_data.get('assigned_worker')}")
        else:
            print(f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] No QUEUED jobs available.")

    except requests.exceptions.Timeout:
        print(f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: Job polling or claiming timed out.")
    except requests.exceptions.RequestException as e:
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: Job polling or claiming failed - {e}")
    except json.JSONDecodeError:
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: Failed to decode JSON response from job API.")
    except Exception as e:
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] An unexpected error occurred during job polling/claiming: {e}")


if __name__ == "__main__":
    print("Sethlans Reborn Worker Agent Starting...")
    initial_system_info = get_system_info()

    while True:
        if not WORKER_INFO:
            send_heartbeat(initial_system_info)
        else:
            send_heartbeat({'hostname': WORKER_INFO['hostname']})

        get_and_claim_job()

        time.sleep(min(HEARTBEAT_INTERVAL_SECONDS, JOB_POLLING_INTERVAL_SECONDS))