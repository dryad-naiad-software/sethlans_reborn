# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Handles system monitoring and communication between the worker and manager.

This module is responsible for:
- Registering the worker with the central manager.
- Enrolling with the manager using a pre-shared key (when no token exists).
- Sending periodic heartbeats to maintain a live connection.
- Ensuring the required Blender LTS version is installed before registration.

Hardware detection has been extracted to the hardware_detection module.
Enrollment logic has been extracted to the enrollment module.
Functions are re-exported here for backward compatibility.
"""

import logging

from sethlans_worker_agent import config
from sethlans_worker_agent import api_handler
from sethlans_worker_agent.enrollment import check_auth_config, enroll_with_manager
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


def _is_loopback(addr):
    """Check if an address is a loopback address."""
    return addr in ('127.0.0.1', 'localhost', '::1')


def _get_ui_url():
    """
    Build the UI URL for heartbeat payloads.

    Returns None if UI is disabled or bound to a loopback address
    (since the link would be useless from the manager's browser).
    """
    if not config.UI_ENABLED:
        return None
    if _is_loopback(config.UI_BIND_ADDRESS):
        return None
    if config.UI_BIND_ADDRESS == '0.0.0.0':
        ui_host = IP_ADDRESS
    else:
        ui_host = config.UI_BIND_ADDRESS
    return f"http://{ui_host}:{config.UI_PORT}"


def _find_latest_lts_patch(all_versions, lts_series):
    """
    Helper to find the highest patch version for a given LTS series.

    Args:
        all_versions (list): All available Blender version strings.
        lts_series (str): The major.minor series (e.g., '4.5').

    Returns:
        str or None: The latest patch version string, or None.
    """
    lts_patches = [
        v for v in all_versions if v.startswith(lts_series + '.')
    ]
    if not lts_patches:
        return None
    return sorted(
        lts_patches,
        key=lambda v: [int(p) for p in v.split('.')],
        reverse=True
    )[0]


def _ensure_blender_available():
    """
    Ensure the latest Blender LTS version is downloaded and available.

    Returns:
        The Blender executable path, or None on failure.
    """
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
            f"{config.REQUIRED_LTS_VERSION_SERIES}. "
            f"Registration aborted."
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

    return lts_blender


def register_with_manager():
    """
    Ensures Blender is available, then registers/enrolls with the manager.

    Authentication flow:
    1. If API_TOKEN is set, use it for a standard authenticated heartbeat.
    2. If API_TOKEN is empty but ENROLLMENT_KEY is set, perform enrollment.
    3. If neither is set, log a critical error and return None.

    Enrollment MUST complete before the worker enters the polling loop.
    Blender downloads come from download.blender.org (not manager API),
    so they don't need auth and happen before enrollment.

    Returns:
        int or None: The worker ID, or None on failure.
    """
    global WORKER_ID

    if not _ensure_blender_available():
        return None

    auth_state = check_auth_config()
    payload = get_system_info()
    payload['ui_url'] = _get_ui_url()

    if auth_state == 'none':
        logger.critical(
            "No API token or enrollment key configured. "
            "Cannot communicate with manager."
        )
        return None

    if auth_state == 'enroll':
        logger.info("No API token found. Attempting enrollment...")
        worker_id = enroll_with_manager(payload)
        if worker_id:
            WORKER_ID = worker_id
            return WORKER_ID
        return None

    # auth_state == 'token': authenticated heartbeat with existing token.
    logger.info(
        "Using existing API token for registration heartbeat."
    )
    data = api_handler.send_authenticated_heartbeat(payload)
    if not data:
        logger.error("Registration heartbeat failed.")
        return None

    WORKER_ID = data.get('id')
    if WORKER_ID:
        logger.info(
            f"Heartbeat successful. Worker is registered as "
            f"ID: {WORKER_ID}"
        )
        return WORKER_ID

    logger.error("Heartbeat response did not include a worker ID.")
    return None


def send_heartbeat():
    """
    Sends a periodic heartbeat to the manager using auth headers.
    """
    if not WORKER_ID:
        logger.warning(
            "Cannot send heartbeat, worker is not registered."
        )
        return

    payload = {"hostname": HOSTNAME, "ui_url": _get_ui_url()}
    api_handler.send_authenticated_heartbeat(payload)
