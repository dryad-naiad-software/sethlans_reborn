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
# sethlans_worker_agent/system_monitor.py
"""
Handles all system monitoring and communication between the worker and manager.

This module is responsible for:
- Detecting and caching local hardware and software capabilities.
- Registering the worker with the central manager.
- Sending periodic heartbeats to maintain a live connection.
- Ensuring the required Blender LTS version is installed before registration.
"""

import logging
import platform
import socket
import requests
import subprocess
import tempfile
import os
import sys
import json
import re
from collections import defaultdict
import psutil  # --- NEW ---
from sethlans_worker_agent import config
from sethlans_worker_agent.tool_manager import tool_manager_instance
from sethlans_worker_agent.utils import blender_release_parser

logger = logging.getLogger(__name__)

# --- Module-level state and cache ---
WORKER_ID = None
HOSTNAME = socket.gethostname()
IP_ADDRESS = socket.gethostbyname(HOSTNAME)
OS_INFO = f"{platform.system()} {platform.release()}"
_gpu_devices_cache = None
_gpu_details_cache = None
_cpu_thread_count_cache = None  # --- NEW ---


def _filter_preferred_gpus(devices):
    """
    Filters a raw list of Blender devices to one preferred entry per physical card.

    - Groups devices by a physical identifier.
    - For each physical card, it selects the best backend:
      - Prefers 'OPTIX' for NVIDIA cards with 'RTX' in the name.
      - Prefers 'CUDA' for all other NVIDIA cards.
      - Falls back to a default preference list for non-NVIDIA cards.

    Args:
        devices (list): The raw list of device dictionaries from Blender.

    Returns:
        list: A filtered and sorted list containing one device dictionary
              per physical GPU.
    """
    physical_gpus = defaultdict(list)
    for device in devices:
        device_id_str = device.get('id', '')
        # The true physical ID is the base ID before the optional _OptiX suffix
        physical_id = device_id_str.removesuffix('_OptiX')
        physical_gpus[physical_id].append(device)

    preferred_devices = []
    for physical_id, device_options in physical_gpus.items():
        if not device_options:
            continue

        best_option = None
        device_name = device_options[0].get('name', '').upper()
        available_backends = {d['type'] for d in device_options}

        # --- NEW: Implement user-specified preference logic ---
        if 'NVIDIA' in device_name:
            if 'RTX' in device_name and 'OPTIX' in available_backends:
                best_option = next((d for d in device_options if d['type'] == 'OPTIX'), None)

            # Fallback for non-RTX cards or if OptiX isn't available
            if not best_option and 'CUDA' in available_backends:
                best_option = next((d for d in device_options if d['type'] == 'CUDA'), None)

        # Fallback for non-NVIDIA cards (e.g., AMD, Intel) or if the above logic somehow fails
        if not best_option:
            backend_preference = ['OPTIX', 'CUDA', 'HIP', 'METAL', 'ONEAPI']
            for backend in backend_preference:
                if backend in available_backends:
                    best_option = next((d for d in device_options if d['type'] == backend), None)
                    if best_option:
                        break

        if best_option:
            preferred_devices.append(best_option)

    # Sort by the Blender device index to maintain a stable order
    return sorted(preferred_devices, key=lambda d: d['index'])


def get_gpu_device_details():
    """
    Executes the detect_gpus.py script to get detailed info about each GPU.

    This function runs a headless Blender instance to execute the script,
    captures its standard output, and robustly parses it to find the JSON
    array containing the GPU device information. The result is cached. It also
    logs a summary of the detected physical GPUs and their preferred backends
    upon successful detection.

    Returns:
        list: A list of dictionaries, where each dictionary contains details
              about a detected GPU device (name, type, id, etc.). Returns an
              empty list on failure.
    """
    global _gpu_details_cache
    if _gpu_details_cache is not None:
        return _gpu_details_cache

    blender_exe = tool_manager_instance.ensure_blender_version_available(config.REQUIRED_LTS_VERSION_SERIES)
    if not blender_exe:
        logger.error("Cannot get GPU details: Blender executable not found.")
        _gpu_details_cache = []
        return _gpu_details_cache

    script_path = os.path.join(os.path.dirname(__file__), 'utils', 'detect_gpus.py')
    command = [blender_exe, '--background', '--factory-startup', '--python', script_path]

    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=90)

        logger.debug(f"detect_gpus.py stdout:\n{result.stdout}")
        logger.debug(f"detect_gpus.py stderr:\n{result.stderr}")

        json_line = None
        for line in result.stdout.strip().splitlines():
            if line.strip().startswith('[') and line.strip().endswith(']'):
                json_line = line
                break

        if json_line:
            raw_devices = json.loads(json_line)
            preferred_devices = _filter_preferred_gpus(raw_devices)
            logger.info(
                f"Detected {len(raw_devices)} logical devices, filtered to {len(preferred_devices)} preferred physical devices.")

            if preferred_devices:
                logger.info(f"Physical GPU detection complete. Found {len(preferred_devices)} physical GPUs.")
                for i, device in enumerate(preferred_devices):
                    name = device.get('name', 'N/A')
                    backend = device.get('type', 'N/A')
                    dev_id = device.get('id', 'N/A')
                    logger.info(
                        f"  - [Physical GPU {i}] Name: {name} | Preferred Backend: {backend} | Blender Device ID: {dev_id}")

            _gpu_details_cache = preferred_devices
            return _gpu_details_cache
        else:
            logger.warning("Could not find a valid JSON line in detect_gpus.py output.")
            _gpu_details_cache = []
            return []

    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        logger.error(f"Failed to execute or parse detect_gpus.py script: {e}")
        _gpu_details_cache = []
        return _gpu_details_cache


def detect_gpu_devices():
    """
    Detects available Cycles GPU rendering backends (e.g., CUDA, OPTIX).

    This function is a lightweight wrapper around `get_gpu_device_details`.
    It calls the more detailed detection function and then extracts the unique
    set of device types (backends) from the results. The result is cached.

    Returns:
        list: A list of unique strings for the detected GPU backends.
    """
    global _gpu_devices_cache
    if _gpu_devices_cache is not None:
        return _gpu_devices_cache

    if config.FORCE_CPU_ONLY:
        logger.warning("Worker is in FORCE_CPU_ONLY mode. Reporting no GPU devices.")
        _gpu_devices_cache = []
        return _gpu_devices_cache

    preferred_gpus = get_gpu_device_details()

    if preferred_gpus:
        unique_backends = sorted(list(set(device['type'] for device in preferred_gpus)))
        _gpu_devices_cache = unique_backends
    else:
        _gpu_devices_cache = []

    logger.info(f"Detected and cached GPU backends: {_gpu_devices_cache if _gpu_devices_cache else 'None'}")
    return _gpu_devices_cache


# --- NEW ---
def get_cpu_thread_count():
    """
    Gets the number of logical CPU processors (threads) and caches the result.

    Returns:
        int: The total number of threads available on the system.
    """
    global _cpu_thread_count_cache
    if _cpu_thread_count_cache is not None:
        return _cpu_thread_count_cache

    try:
        count = psutil.cpu_count()
        logger.info(f"Detected {count} logical CPU cores (threads).")
        _cpu_thread_count_cache = count
        return count
    except Exception as e:
        logger.error(f"Could not determine CPU thread count. Falling back to 1. Error: {e}")
        _cpu_thread_count_cache = 1
        return 1
# --- END NEW ---


def get_system_info():
    """
    Gathers a snapshot of the worker's system information.

    Returns:
        dict: A dictionary containing the hostname, IP, OS, and available tools,
              now including detailed GPU information.
    """
    available_blenders = tool_manager_instance.scan_for_local_blenders()
    gpu_devices = detect_gpu_devices()
    gpu_details = get_gpu_device_details()

    return {
        "hostname": HOSTNAME,
        "ip_address": IP_ADDRESS,
        "os": OS_INFO,
        "available_tools": {
            "blender": [v['version'] for v in available_blenders],
            "gpu_devices": gpu_devices,
            "gpu_devices_details": gpu_details
        }
    }


def _find_latest_lts_patch(all_versions, lts_series):
    """
    Helper function to find the highest patch version for a given major.minor LTS series.

    Args:
        all_versions (list): A list of all available Blender version strings (e.g., '4.5.1').
        lts_series (str): The major.minor version series to check (e.g., '4.5').

    Returns:
        str or None: The latest patch version string, or None if none are found.
    """
    lts_patches = [v for v in all_versions if v.startswith(lts_series + '.')]
    if not lts_patches:
        return None
    return sorted(lts_patches, key=lambda v: [int(p) for p in v.split('.')], reverse=True)[0]


def register_with_manager():
    """
    Ensures the latest Blender LTS version is installed and then registers the worker with the manager.

    This is a critical bootstrap function that runs at the start of the worker agent's lifecycle.
    It first attempts to find or download the required Blender version before sending a
    full system information payload to the manager.

    Returns:
        int or None: The ID assigned to the worker by the manager, or None if registration fails.
    """
    global WORKER_ID

    logger.info(f"Ensuring latest Blender LTS ({config.REQUIRED_LTS_VERSION_SERIES}.x) is available...")

    all_releases = blender_release_parser.get_blender_releases()
    if not all_releases:
        logger.critical("Could not fetch Blender release list from download site. Registration aborted.")
        return None

    latest_lts_version = _find_latest_lts_patch(all_releases.keys(), config.REQUIRED_LTS_VERSION_SERIES)
    if not latest_lts_version:
        logger.critical(f"No versions found for LTS series {config.REQUIRED_LTS_VERSION_SERIES}. Registration aborted.")
        return None

    lts_blender = tool_manager_instance.ensure_blender_version_available(latest_lts_version)
    if not lts_blender:
        logger.critical(f"Could not acquire Blender {latest_lts_version}. Registration aborted.")
        return None

    heartbeat_url = f"{config.MANAGER_API_URL}heartbeat/"
    payload = get_system_info()

    logger.info(f"Sending registration heartbeat to {heartbeat_url}...")
    try:
        response = requests.post(heartbeat_url, json=payload, timeout=10)
        response.raise_for_status()

        data = response.json()
        WORKER_ID = data.get('id')

        if WORKER_ID:
            logger.info(f"Heartbeat successful. Worker is registered as ID: {WORKER_ID}")
            return WORKER_ID
        else:
            logger.error("Heartbeat response did not include a worker ID.")
            return None

    except requests.exceptions.RequestException as e:
        logger.error(f"Could not send registration heartbeat: {e}")
        return None


def send_heartbeat():
    """
    Sends a simple, periodic heartbeat to the manager to confirm the worker is alive.

    This function only sends the worker's hostname to the `/api/heartbeat/` endpoint,
    relying on the manager to update the `last_seen` timestamp for the corresponding
    worker record.
    """
    if not WORKER_ID:
        logger.warning("Cannot send heartbeat, worker is not registered.")
        return

    heartbeat_url = f"{config.MANAGER_API_URL}heartbeat/"
    payload = {"hostname": HOSTNAME}

    try:
        response = requests.post(heartbeat_url, json=payload, timeout=5)
        response.raise_for_status()
        logger.debug("Periodic heartbeat successful.")
    except requests.exceptions.RequestException as e:
        logger.warning(f"Could not send periodic heartbeat: {e}")