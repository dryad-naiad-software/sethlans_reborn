# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Detects and caches local hardware and software capabilities.

Responsibilities: GPU device detection via headless Blender subprocess,
GPU filtering to one preferred entry per physical card, CPU/GPU caching,
and gathering system information for worker registration.
"""

import json
import logging
import os
import platform
import socket
import subprocess
from collections import defaultdict

import psutil

from sethlans_worker_agent import config
from sethlans_worker_agent.cpu_detection import get_cpu_name  # noqa: F401
from sethlans_worker_agent.tool_manager import tool_manager_instance

logger = logging.getLogger(__name__)

# --- Module-level constants ---
HOSTNAME = socket.gethostname()
IP_ADDRESS = socket.gethostbyname(HOSTNAME)
OS_INFO = f"{platform.system()} {platform.release()}"

# --- Module-level caches ---
_gpu_devices_cache = None
_gpu_details_cache = None
_cpu_thread_count_cache = None


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

        # --- Implement user-specified preference logic ---
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


def _find_any_blender_executable():
    """Find the executable path for any locally installed Blender version.

    Used for GPU detection which only needs *some* Blender binary, not
    a specific version. Returns None if no versions are installed.
    """
    local_blenders = tool_manager_instance.scan_for_local_blenders()
    if not local_blenders:
        return None
    # Use the first available version.
    version = local_blenders[0]['version']
    return tool_manager_instance.get_blender_executable_path(version)


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

    blender_exe = _find_any_blender_executable()
    if not blender_exe:
        logger.warning("Cannot detect GPUs yet: no Blender executable available.")
        return []  # Don't cache — retry after Blender is downloaded

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
    elif _find_any_blender_executable():
        # Blender is available but found no GPUs — cache the empty result
        _gpu_devices_cache = []
    else:
        # No Blender yet — don't cache, retry after download
        return []

    logger.info(f"Detected and cached GPU backends: {_gpu_devices_cache if _gpu_devices_cache else 'None'}")
    return _gpu_devices_cache


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
        "cpu_name": get_cpu_name(),
        "available_tools": {
            "blender": [v['version'] for v in available_blenders],
            "gpu_devices": gpu_devices,
            "gpu_devices_details": gpu_details
        }
    }
