# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Handles system monitoring and communication between the worker and manager.

This module is responsible for:
- Registering the worker with the central manager.
- Enrolling with the manager using a pre-shared key (when no token exists).
- Sending periodic heartbeats to maintain a live connection.
- Triggering version synchronization based on heartbeat responses.

Hardware detection has been extracted to the hardware_detection module.
Enrollment logic has been extracted to the enrollment module.
Version sync logic lives in the version_sync module.
Functions are re-exported here for backward compatibility.
"""

import logging

from sethlans_worker_agent import config
from sethlans_worker_agent import api_handler
from sethlans_worker_agent.enrollment import check_auth_config, enroll_with_manager
from sethlans_worker_agent import version_sync

# Re-export hardware detection functions for backward compatibility
from sethlans_worker_agent.hardware_detection import (  # noqa: F401
    detect_gpu_devices,
    get_gpu_device_details,
    get_cpu_name,
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

# Tracks whether all required versions are installed and the worker
# is ready to poll for jobs. Set after successful registration download.
_versions_ready = False


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


def _process_heartbeat_versions(response_data, is_busy=False, active_jobs=None):
    """
    Process version requirements from a heartbeat response.

    Downloads missing versions and cleans up unrequired ones.
    When is_busy is True, downloads are queued for later (P2-F6).

    Returns True if at least one required version is installed.
    """
    global _versions_ready
    if active_jobs is None:
        active_jobs = {}

    ready = version_sync.sync_versions(response_data, is_busy, active_jobs)
    _versions_ready = ready
    return ready


def register_with_manager():
    """
    Registers/enrolls with the manager, then downloads required versions.

    Authentication flow:
    1. If API_TOKEN is set, use it for a standard authenticated heartbeat.
    2. If API_TOKEN is empty but ENROLLMENT_KEY is set, perform enrollment.
    3. If neither is set, log a critical error and return None.

    After successful heartbeat, downloads ALL required Blender versions
    before allowing the worker to poll for jobs (P2-F4).

    Returns:
        int or None: The worker ID, or None on failure.
    """
    global WORKER_ID, _versions_ready

    auth_state = check_auth_config()
    payload = get_system_info()
    payload['ui_url'] = _get_ui_url()
    payload['status'] = 'IDLE'

    if auth_state == 'none':
        logger.critical(
            "No API token or enrollment key configured. "
            "Cannot communicate with manager."
        )
        return None

    response_data = None

    if auth_state == 'enroll':
        logger.info("No API token found. Attempting enrollment...")
        worker_id = enroll_with_manager(payload)
        if not worker_id:
            return None
        WORKER_ID = worker_id
        # After enrollment, send an authenticated heartbeat to get versions.
        response_data = api_handler.send_authenticated_heartbeat(payload)
    else:
        # auth_state == 'token': authenticated heartbeat with existing token.
        logger.info("Using existing API token for registration heartbeat.")
        response_data = api_handler.send_authenticated_heartbeat(payload)
        if not response_data:
            logger.error("Registration heartbeat failed.")
            return None

        WORKER_ID = response_data.get('id')
        if not WORKER_ID:
            logger.error("Heartbeat response did not include a worker ID.")
            return None

    logger.info(f"Heartbeat successful. Worker is registered as ID: {WORKER_ID}")

    # Download all required versions before entering the poll loop (P2-F4).
    if response_data:
        ready = _process_heartbeat_versions(response_data)
        if not ready:
            logger.critical(
                "Failed to install any required Blender versions. "
                "Will retry on next heartbeat cycle."
            )
            # Return the worker ID but mark versions as not ready.
            # The main loop will skip polling until versions are available.
            _versions_ready = False
    else:
        logger.warning("No heartbeat response data for version sync.")
        _versions_ready = False

    return WORKER_ID


def send_heartbeat(is_busy=False, active_jobs=None):
    """
    Sends a periodic heartbeat to the manager using auth headers.

    Includes the full system info (with available_tools) so the
    manager always has up-to-date version lists for claim validation.

    Processes the required_blender_versions from the response to
    keep installed versions in sync with manager requirements.

    Args:
        is_busy: True if the worker is currently rendering.
        active_jobs: Dict of active jobs for in-use version checks.
    """
    global _versions_ready
    if not WORKER_ID:
        logger.warning("Cannot send heartbeat, worker is not registered.")
        return

    payload = get_system_info()
    payload.pop('os', None)
    payload['ui_url'] = _get_ui_url()
    payload['status'] = 'RENDERING' if is_busy else 'IDLE'

    response_data = api_handler.send_authenticated_heartbeat(payload)
    if response_data:
        ready = _process_heartbeat_versions(
            response_data, is_busy=is_busy,
            active_jobs=active_jobs or {}
        )
        _versions_ready = ready


def are_versions_ready():
    """Return True if at least one required Blender version is installed."""
    return _versions_ready
