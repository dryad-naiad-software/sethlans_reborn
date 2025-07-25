# sethlans_worker_agent/system_monitor.py

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

import logging
import platform
import socket
import requests
import subprocess  # --- NEW IMPORT ---
from sethlans_worker_agent import config
from sethlans_worker_agent.tool_manager import tool_manager_instance  # --- NEW IMPORT ---

logger = logging.getLogger(__name__)

# Module-level state
WORKER_ID = None
HOSTNAME = socket.gethostname()
IP_ADDRESS = socket.gethostbyname(HOSTNAME)
OS_INFO = f"{platform.system()} {platform.release()}"


# --- NEW FUNCTION ---
def detect_gpu_devices():
    """
    Uses Blender to detect available Cycles GPU rendering devices.
    Returns a list of device types (e.g., ['CUDA', 'OPTIX']).
    """
    logger.info("Detecting available GPU devices using Blender...")

    # Find an available blender executable to run the check
    local_blenders = tool_manager_instance.scan_for_local_blenders()
    if not local_blenders:
        logger.warning("No local Blender versions found, cannot detect GPU devices.")
        return []

    # Prefer the latest version for detection
    latest_version = sorted([b['version'] for b in local_blenders], reverse=True)[0]
    blender_exe = tool_manager_instance.get_blender_executable_path(latest_version)

    if not blender_exe:
        logger.error(f"Could not get executable path for Blender {latest_version}.")
        return []

    # Python script to be executed by Blender
    py_script = (
        "import bpy, sys; "
        "devices = set();"
        "prefs = bpy.context.preferences.addons['cycles'].preferences; "
        "for device_type in prefs.get_device_types(): "
        "    if device_type[0] != 'CPU': devices.add(device_type[0]); "
        "print(','.join(sorted(list(devices))));"
    )

    command = [blender_exe, '--background', '--factory-startup', '--python-expr', py_script]

    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=60)
        gpu_devices = result.stdout.strip().split(',') if result.stdout.strip() else []
        logger.info(f"Detected GPU Devices: {gpu_devices if gpu_devices else 'None'}")
        return gpu_devices
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        logger.error(f"Failed to detect GPU devices using Blender: {e}")
        return []
    except FileNotFoundError:
        logger.error(f"Blender executable not found at {blender_exe} for GPU detection.")
        return []


# --- UPDATED FUNCTION ---
def get_system_info():
    """Gathers basic system information, now including GPU devices."""
    available_blenders = tool_manager_instance.scan_for_local_blenders()
    gpu_devices = detect_gpu_devices()  # <-- New call

    return {
        "hostname": HOSTNAME,
        "ip_address": IP_ADDRESS,
        "os": OS_INFO,
        "available_tools": {
            "blender": [v['version'] for v in available_blenders],
            "gpu_devices": gpu_devices  # <-- New data
        }
    }


def register_with_manager():
    """
    Registers the worker with the manager and retrieves the worker ID.
    Returns the worker ID on success, None on failure.
    """
    global WORKER_ID
    heartbeat_url = f"{config.MANAGER_API_URL}heartbeat/"
    payload = get_system_info()  # This now includes GPU info automatically

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
    payload = {"hostname": HOSTNAME}  # Subsequent heartbeats are simpler

    try:
        response = requests.post(heartbeat_url, json=payload, timeout=5)
        response.raise_for_status()
        logger.debug("Periodic heartbeat successful.")
    except requests.exceptions.RequestException as e:
        logger.warning(f"Could not send periodic heartbeat: {e}")