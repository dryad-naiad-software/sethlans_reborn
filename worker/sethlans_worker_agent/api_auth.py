# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Authentication state management for authenticated manager API calls.

Handles:
- Auth header construction for manager API requests.
- Auth failure detection (401/403) and the polling-stop flag.
- Authenticated heartbeat API call.
- Retention of rendered output when uploads fail due to auth errors.

Enrollment-specific helpers have moved to
``sethlans_worker_agent.enrollment_client`` and are no longer part of
this module.
"""
import logging
import os
import shutil
import threading
from pathlib import Path
from typing import Optional, Dict, Any

import requests

from sethlans_worker_agent import config

logger = logging.getLogger(__name__)

# Flag set when a 401 is received, signaling the main loop to stop polling.
_auth_failed = threading.Event()


def is_auth_failed():
    """Return True if an authentication failure has been detected."""
    return _auth_failed.is_set()


def get_auth_headers():
    """
    Return authentication headers for manager API requests.

    Returns a dict with the Authorization header when an API token
    is configured, or an empty dict otherwise.
    """
    if config.API_TOKEN:
        return {"Authorization": f"Token {config.API_TOKEN}"}
    return {}


def handle_auth_response(response):
    """
    Check for authentication/authorization failures.

    Returns True if the response indicates an auth error that
    should abort the current operation.
    """
    if response.status_code == 401:
        logger.error(
            "Authentication failed. Token may be revoked. "
            "Stopping job polling."
        )
        _auth_failed.set()
        return True
    if response.status_code == 403:
        logger.warning("Permission denied for this operation.")
        return True
    return False


def retain_failed_upload(job_id: int, output_file_path: str):
    """
    Copy a rendered output file to the failed_uploads/ directory
    when upload fails due to auth error, so output is not lost.
    """
    try:
        failed_dir = Path(config.FAILED_UPLOADS_DIR)
        failed_dir.mkdir(parents=True, exist_ok=True)
        dest = failed_dir / os.path.basename(output_file_path)
        shutil.copy2(output_file_path, dest)
        logger.error(
            f"Upload failed (auth error). Output retained at: {dest}"
        )
    except OSError as e:
        logger.error(
            f"Failed to retain output for job {job_id}: {e}"
        )


def send_authenticated_heartbeat(
    retry_func, payload: Dict[str, Any]
) -> Optional[Dict]:
    """
    Send a heartbeat with the API token for an already-enrolled worker.

    Args:
        retry_func: The _retry_request function from api_handler.
        payload: System info dict for the heartbeat.

    Returns:
        The response JSON dict on success, or None on failure.
    """
    heartbeat_url = f"{config.MANAGER_API_URL}heartbeat/"
    try:
        response = retry_func(
            'post', heartbeat_url,
            json=payload, headers=get_auth_headers(), timeout=5
        )
        if handle_auth_response(response):
            return None
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.warning(f"Could not send periodic heartbeat: {e}")
    return None
