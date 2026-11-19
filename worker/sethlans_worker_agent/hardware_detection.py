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
import threading
from collections import defaultdict

import psutil

from sethlans_worker_agent.cpu_detection import get_cpu_name  # noqa: F401
from sethlans_worker_agent.gpu_select_helper import KNOWN_BACKENDS
from sethlans_worker_agent.tool_manager import tool_manager_instance

logger = logging.getLogger(__name__)

# --- Module-level constants ---
HOSTNAME = socket.gethostname()


def _resolve_ip_address(hostname, timeout_seconds=5.0):
    """Resolve hostname to an IPv4 address with a hard timeout.

    macOS ``.local`` hostnames go through mDNSResponder, which can block
    indefinitely on ephemeral runners or machines with a flaky network
    setup (issue #122). ``socket.gethostbyname`` has no timeout
    parameter, so we run it in a daemon helper thread and fall back to
    ``127.0.0.1`` if the lookup does not finish in time. The daemon
    thread is abandoned on timeout; it does not prevent process exit.
    """
    result = {"value": None}

    def _lookup():
        try:
            result["value"] = socket.gethostbyname(hostname)
        except OSError:
            pass

    t = threading.Thread(target=_lookup, name="ip-resolve", daemon=True)
    t.start()
    t.join(timeout=timeout_seconds)
    return result["value"] or "127.0.0.1"


IP_ADDRESS = _resolve_ip_address(HOSTNAME)
OS_INFO = f"{platform.system()} {platform.release()}"

# --- Module-level caches ---
_gpu_devices_cache = None
_gpu_details_cache = None
_gpu_details_blender_exe = None  # Blender that filled _gpu_details_cache (#239 drift check)
_gpu_details_lock = threading.Lock()  # first fill of the two above, and later executable changes (#253)
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

        device_name = device_options[0].get('name', '').upper()
        available_backends = {d['type'] for d in device_options}
        best_option = (_nvidia_choice(device_name, device_options, available_backends)
                       if 'NVIDIA' in device_name else None)

        # Fallback for non-NVIDIA cards (e.g., AMD, Intel) or if the above logic somehow fails
        if not best_option:
            for backend in KNOWN_BACKENDS:
                if backend in available_backends:
                    best_option = next((d for d in device_options if d['type'] == backend), None)
                    if best_option:
                        break

        if best_option:
            preferred_devices.append(best_option)

    # Sort by the Blender device index to maintain a stable order
    return sorted(preferred_devices, key=lambda d: d['index'])


def _nvidia_choice(device_name, device_options, available_backends):
    """OptiX for an RTX card that offers it, else CUDA when offered, else None."""
    if 'RTX' in device_name and 'OPTIX' in available_backends:
        choice = next((d for d in device_options if d['type'] == 'OPTIX'), None)
        if choice:
            return choice
    # Non-RTX cards, or OptiX not available
    if 'CUDA' in available_backends:
        return next((d for d in device_options if d['type'] == 'CUDA'), None)
    return None


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
    """Run detect_gpus.py once and return the physical GPUs (cached; [] on failure).

    Each record has the card's ``name``, preferred backend ``type``, Blender
    device ``id`` and ``index``. The first fill is serialized under
    ``_gpu_details_lock``: a second caller (the agent loop and the web UI
    status thread can both get here first) waits for the running detection
    instead of starting its own, so the list and the Blender that produced
    it always come from one detection and a filled cache is never replaced.
    Without an installed Blender nothing is cached and the next call retries.
    The lock is held only here and in ``capacity.drift`` when it adopts a new
    detection executable, never with a worker lock held; while it is held only
    ``tool_manager._download_lock`` (a leaf, in ``scan_for_local_blenders``) is taken.
    """
    if _gpu_details_cache is not None:
        return _gpu_details_cache
    with _gpu_details_lock:
        if _gpu_details_cache is None:
            _fill_gpu_details_cache()
        return _gpu_details_cache if _gpu_details_cache is not None else []


def _fill_gpu_details_cache():
    """Detect the GPUs and cache the list with its Blender; caller holds the lock."""
    global _gpu_details_cache, _gpu_details_blender_exe
    blender_exe = _find_any_blender_executable()
    if not blender_exe:
        logger.info("Cannot detect GPUs yet: no Blender executable available.")
        return  # Don't cache: retry after Blender is downloaded

    script_path = os.path.join(os.path.dirname(__file__), 'utils', 'detect_gpus.py')
    command = [blender_exe, '--background', '--factory-startup', '--python', script_path]
    try:
        from sethlans_worker_agent import env_cleanup
        result = subprocess.run(  # Blender's Python writes UTF-8 whatever the locale
            command, capture_output=True, text=True, encoding='utf-8', errors='replace', check=True, timeout=90,
            env=env_cleanup.clean_env_for_blender(),
        )
        logger.debug(f"detect_gpus.py stdout:\n{result.stdout}")
        logger.debug(f"detect_gpus.py stderr:\n{result.stderr}")
        json_line = next((line for line in result.stdout.strip().splitlines()
                          if line.strip().startswith('[') and line.strip().endswith(']')), None)
        if not json_line:
            logger.warning("Could not find a valid JSON line in detect_gpus.py output.")
            _gpu_details_cache = []
            return
        raw_devices = json.loads(json_line)
    except (subprocess.SubprocessError, OSError, ValueError) as e:
        # Cached as "no GPUs" so callers queued on the lock do not repeat the detection.
        logger.error("Failed to execute or parse detect_gpus.py (%s); the worker will run without GPUs "
                     "until it is restarted.", e, exc_info=True)
        _gpu_details_cache = []
        return
    try:
        preferred_devices = _filter_preferred_gpus(raw_devices)
    except (KeyError, TypeError, AttributeError) as e:  # a malformed device record; cached as above
        logger.error("detect_gpus.py returned an unusable device list (%s); the worker will run without GPUs "
                     "until it is restarted.", e, exc_info=True)
        _gpu_details_cache = []
        return

    logger.info("Detected %d logical devices, filtered to %d preferred.", len(raw_devices), len(preferred_devices))
    if preferred_devices:
        logger.info(f"Physical GPU detection complete. Found {len(preferred_devices)} physical GPUs.")
    for i, device in enumerate(preferred_devices):
        logger.info(f"  - [Physical GPU {i}] Name: {device.get('name', 'N/A')} | Preferred Backend: "
                    f"{device.get('type', 'N/A')} | Blender Device ID: {device.get('id', 'N/A')}")
    # Executable first: a reader on the unlocked fast path never sees a list without it.
    _gpu_details_blender_exe = blender_exe
    _gpu_details_cache = preferred_devices


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
