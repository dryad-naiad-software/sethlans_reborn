# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Authentication state management and enrollment API calls.

Handles:
- Auth header construction for manager API requests.
- Auth failure detection (401/403) and the polling-stop flag.
- Enrollment heartbeat and authenticated heartbeat API calls.
- Retention of rendered output when uploads fail due to auth errors.
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


def get_enrollment_headers():
    """Return headers for enrollment requests using the enrollment key."""
    if config.ENROLLMENT_KEY:
        return {"X-Enrollment-Key": config.ENROLLMENT_KEY}
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


def send_enrollment_heartbeat(
    retry_func, payload: Dict[str, Any]
) -> Optional[Dict]:
    """
    Send an enrollment heartbeat with the X-Enrollment-Key header.

    Args:
        retry_func: The _retry_request function from api_handler.
        payload: System info dict for registration.

    Returns:
        The response JSON dict on success (contains 'token'),
        or None on failure.
    """
    heartbeat_url = f"{config.MANAGER_API_URL}heartbeat/"
    headers = get_enrollment_headers()
    if not headers:
        logger.error("No enrollment key configured.")
        return None

    logger.info(
        f"Sending enrollment heartbeat to {heartbeat_url}..."
    )
    try:
        response = retry_func(
            requests.post, heartbeat_url,
            json=payload, headers=headers, timeout=10
        )
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 403:
            logger.error(
                "Enrollment failed: invalid enrollment key."
            )
        elif response.status_code == 429:
            logger.warning(
                "Enrollment rate limited. Try again later."
            )
        else:
            logger.error(
                f"Enrollment failed. Status: {response.status_code}, "
                f"Response: {response.text}"
            )
    except requests.exceptions.RequestException as e:
        logger.error(f"Could not send enrollment heartbeat: {e}")
    return None


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
            requests.post, heartbeat_url,
            json=payload, headers=get_auth_headers(), timeout=5
        )
        if handle_auth_response(response):
            return None
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.warning(f"Could not send periodic heartbeat: {e}")
    return None
