# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Handles system monitoring and communication between the worker and manager.

This module is responsible for:
- Registering the worker with the central manager via an authenticated heartbeat.
- Sending periodic heartbeats to maintain a live connection.
- Triggering version synchronization based on heartbeat responses.

Enrollment is handled by the first-run wizard (see ``wizard.py`` /
``enrollment_client.py``). By the time ``register_with_manager()`` is
called the worker has already obtained an API token via the wizard,
so this module only exercises the authenticated heartbeat path.
"""

import logging

from sethlans_worker_agent import config
from sethlans_worker_agent import api_handler
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


def _get_ui_cert_fingerprint():
    """Return the worker UI TLS cert fingerprint, or '' if unavailable."""
    from sethlans_worker_agent import tls_setup
    return tls_setup.get_ui_cert_fingerprint() or ''


def _is_loopback(addr):
    """Check if an address is a loopback address."""
    return addr in ('127.0.0.1', 'localhost', '::1')


def _get_ui_url():
    """
    Build the UI URL for heartbeat payloads.

    Returns None only if UI is disabled. Uses the worker's IP address
    so the manager UI can link to the worker dashboard.
    """
    if not config.UI_ENABLED:
        return None
    if config.UI_BIND_ADDRESS in ('0.0.0.0', '127.0.0.1', 'localhost', '::1'):
        ui_host = IP_ADDRESS
    else:
        ui_host = config.UI_BIND_ADDRESS
    return f"https://{ui_host}:{config.UI_PORT}"


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
    Register with the manager via an authenticated heartbeat.

    This function assumes the first-run wizard has already completed
    enrollment and persisted an API token to the config store. If the
    token is missing, registration fails — the caller should invoke
    the wizard (agent.py does this on startup).

    After the first successful heartbeat, downloads ALL required
    Blender versions before allowing the worker to poll for jobs
    (P2-F4).

    Returns:
        int or None: The worker ID, or None on failure.
    """
    global WORKER_ID, _versions_ready

    if not config.API_TOKEN:
        logger.critical(
            "No API token configured. The enrollment wizard must run "
            "before register_with_manager()."
        )
        return None

    payload = get_system_info()
    payload['ui_url'] = _get_ui_url()
    payload['ui_cert_fingerprint'] = _get_ui_cert_fingerprint()
    payload['status'] = 'IDLE'

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
    payload['ui_cert_fingerprint'] = _get_ui_cert_fingerprint()
    payload['status'] = 'RENDERING' if is_busy else 'IDLE'

    # Include schedule config in heartbeat (FR-10a).
    schedule_cfg = config.get_schedule_config()
    if schedule_cfg:
        payload['schedule'] = schedule_cfg

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
