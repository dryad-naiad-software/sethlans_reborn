# sethlans_worker_agent/system_monitor.py

import logging
import platform
import socket
import requests
import subprocess
import tempfile
import os
from sethlans_worker_agent import config
from sethlans_worker_agent.tool_manager import tool_manager_instance
from sethlans_worker_agent.utils import blender_release_parser

logger = logging.getLogger(__name__)

# Module-level state
WORKER_ID = None
HOSTNAME = socket.gethostname()
IP_ADDRESS = socket.gethostbyname(HOSTNAME)
OS_INFO = f"{platform.system()} {platform.release()}"


def detect_gpu_devices():
    """
    Uses Blender to detect available Cycles GPU rendering devices based on saved preferences.
    """
    logger.info("Detecting available GPU devices using Blender...")

    local_blenders = tool_manager_instance.scan_for_local_blenders()
    if not local_blenders:
        logger.warning("No local Blender versions found to perform GPU check.")
        return []

    latest_version = \
    sorted([b['version'] for b in local_blenders], key=lambda v: [int(p) for p in v.split('.')], reverse=True)[0]
    blender_exe = tool_manager_instance.get_blender_executable_path(latest_version)

    if not blender_exe:
        logger.error(f"Could not get executable path for Blender {latest_version}.")
        return []

    # This script now simply reads the pre-configured state.
    py_script = """
import bpy
import sys

try:
    found_device_types = set()
    prefs = bpy.context.preferences.addons['cycles'].preferences

    # Check the actual devices Blender has discovered based on saved preferences
    for device in prefs.devices:
        if device.use and device.type != 'CPU':
            # Report the backend type (e.g. 'HIP', 'CUDA') for the active GPU device
            found_device_types.add(prefs.compute_device_type)

    print(','.join(sorted(list(found_device_types))))

except Exception as e:
    print(f"Error during GPU detection script: {e}", file=sys.stderr)
"""

    temp_script_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            temp_script_path = f.name
            f.write(py_script)

        # REMOVED '--factory-startup' to allow Blender to load saved user preferences
        command = [blender_exe, '--background', '--python', temp_script_path]
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=90)

        logger.debug(f"Blender GPU detection stdout:\n{result.stdout}")
        logger.debug(f"Blender GPU detection stderr:\n{result.stderr}")

        gpu_devices = []
        output_lines = result.stdout.strip().splitlines()
        for line in output_lines:
            if ' ' not in line and any(device in line for device in ['CUDA', 'OPTIX', 'HIP', 'METAL', 'ONEAPI']):
                gpu_devices = line.strip().split(',')
                break

        logger.info(f"Detected GPU Devices: {gpu_devices if gpu_devices else 'None'}")
        return [device for device in gpu_devices if device]
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        if e.stdout: logger.error(f"Blender GPU detection stdout on error:\n{e.stdout}")
        if e.stderr: logger.error(f"Blender GPU detection stderr on error:\n{e.stderr}")
        logger.error(f"Failed to detect GPU devices using Blender: {e}")
        return []
    except FileNotFoundError:
        logger.error(f"Blender executable not found at {blender_exe} for GPU detection.")
        return []
    finally:
        if temp_script_path and os.path.exists(temp_script_path):
            os.remove(temp_script_path)


def get_system_info():
    """Gathers basic system information, now including GPU devices."""
    available_blenders = tool_manager_instance.scan_for_local_blenders()
    gpu_devices = detect_gpu_devices()

    return {
        "hostname": HOSTNAME,
        "ip_address": IP_ADDRESS,
        "os": OS_INFO,
        "available_tools": {
            "blender": [v['version'] for v in available_blenders],
            "gpu_devices": gpu_devices
        }
    }


def _find_latest_lts_patch(all_versions, lts_series):
    """Finds the highest patch version for a given major.minor series."""
    lts_patches = [v for v in all_versions if v.startswith(lts_series + '.')]
    if not lts_patches:
        return None
    return sorted(lts_patches, key=lambda v: [int(p) for p in v.split('.')], reverse=True)[0]


def register_with_manager():
    """
    Ensures the latest LTS Blender is present, then registers with the manager.
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
    """Sends a periodic heartbeat to the manager to show the worker is alive."""
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