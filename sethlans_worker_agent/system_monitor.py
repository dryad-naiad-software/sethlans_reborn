# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
# Created by Mario Estrella on 07/22/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
# sethlans_worker_agent/system_monitor.py
"""
Handles system monitoring and communication between the worker and manager.

This module is responsible for:
- Registering the worker with the central manager.
- Sending periodic heartbeats to maintain a live connection.
- Ensuring the required Blender LTS version is installed before registration.

Hardware detection has been extracted to the hardware_detection module.
Functions are re-exported here for backward compatibility.
"""

import logging
import requests

from sethlans_worker_agent import config
from sethlans_worker_agent.tool_manager import tool_manager_instance
from sethlans_worker_agent.utils import blender_release_parser

# Re-export hardware detection functions for backward compatibility
from sethlans_worker_agent.hardware_detection import (  # noqa: F401
    detect_gpu_devices,
    get_gpu_device_details,
    get_cpu_thread_count,
    get_system_info,
    _filter_preferred_gpus,
    HOSTNAME,
    IP_ADDRESS,
    OS_INFO,
)

logger = logging.getLogger(__name__)

# --- Module-level state ---
WORKER_ID = None


def _find_latest_lts_patch(all_versions, lts_series):
    """
    Helper function to find the highest patch version for a given LTS series.

    Args:
        all_versions (list): A list of all available Blender version strings.
        lts_series (str): The major.minor version series to check (e.g., '4.5').

    Returns:
        str or None: The latest patch version string, or None if none found.
    """
    lts_patches = [v for v in all_versions if v.startswith(lts_series + '.')]
    if not lts_patches:
        return None
    return sorted(
        lts_patches,
        key=lambda v: [int(p) for p in v.split('.')],
        reverse=True
    )[0]


def register_with_manager():
    """
    Ensures the latest Blender LTS version is installed and registers the worker.

    This is a critical bootstrap function that runs at the start of the worker
    agent's lifecycle. It first attempts to find or download the required
    Blender version before sending a full system information payload to the
    manager.

    Returns:
        int or None: The ID assigned to the worker by the manager, or None
                     if registration fails.
    """
    global WORKER_ID

    logger.info(
        f"Ensuring latest Blender LTS "
        f"({config.REQUIRED_LTS_VERSION_SERIES}.x) is available..."
    )

    all_releases = blender_release_parser.get_blender_releases()
    if not all_releases:
        logger.critical(
            "Could not fetch Blender release list from download site. "
            "Registration aborted."
        )
        return None

    latest_lts_version = _find_latest_lts_patch(
        all_releases.keys(), config.REQUIRED_LTS_VERSION_SERIES
    )
    if not latest_lts_version:
        logger.critical(
            f"No versions found for LTS series "
            f"{config.REQUIRED_LTS_VERSION_SERIES}. Registration aborted."
        )
        return None

    lts_blender = tool_manager_instance.ensure_blender_version_available(
        latest_lts_version
    )
    if not lts_blender:
        logger.critical(
            f"Could not acquire Blender {latest_lts_version}. "
            f"Registration aborted."
        )
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
            logger.info(
                f"Heartbeat successful. Worker is registered as ID: "
                f"{WORKER_ID}"
            )
            return WORKER_ID
        else:
            logger.error("Heartbeat response did not include a worker ID.")
            return None

    except requests.exceptions.RequestException as e:
        logger.error(f"Could not send registration heartbeat: {e}")
        return None


def send_heartbeat():
    """
    Sends a simple, periodic heartbeat to the manager.

    This function only sends the worker's hostname to the `/api/heartbeat/`
    endpoint, relying on the manager to update the `last_seen` timestamp
    for the corresponding worker record.
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
