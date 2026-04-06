# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Handles all direct API communication between the worker agent and the manager.

This module abstracts the underlying `requests` calls into a set of functions
that are specific to the Sethlans Reborn API contract. It is responsible for
polling, claiming, and updating the status of jobs, as well as uploading
render outputs. All manager-bound requests include authentication headers.

Auth state management is delegated to the api_auth module.
"""
import logging
import os
import time
from typing import Optional, Dict, Any, List

import requests

from sethlans_worker_agent import config
from sethlans_worker_agent.api_auth import (
    get_auth_headers,
    handle_auth_response,
    is_auth_failed,
    retain_failed_upload,
    send_enrollment_heartbeat as _send_enrollment_heartbeat,
    send_authenticated_heartbeat as _send_authenticated_heartbeat,
)

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
BACKOFF_BASE = 1  # seconds

# Re-export for backward compatibility and use by agent.py
is_auth_failed = is_auth_failed  # noqa: F811


def _is_retryable(exception=None, response=None):
    """Check if a request failure is transient and worth retrying."""
    if exception is not None:
        return isinstance(exception, (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        ))
    if response is not None:
        # Never retry auth errors -- they are not transient.
        if response.status_code in (401, 403):
            return False
        return response.status_code >= 500
    return False


def _retry_request(request_func, *args, **kwargs):
    """
    Execute a requests call with retry and exponential backoff.

    Retries on connection errors, timeouts, and 5xx responses.
    Does NOT retry on 4xx (client errors) or successful responses.

    Returns:
        The requests.Response object on success or last attempt.

    Raises:
        requests.exceptions.RequestException: If all retries are
        exhausted and the last attempt raised an exception.
    """
    last_exception = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = request_func(*args, **kwargs)
            if _is_retryable(response=response) and attempt < MAX_RETRIES:
                wait = BACKOFF_BASE * (2 ** (attempt - 1))
                logger.warning(
                    f"Retry {attempt}/{MAX_RETRIES}: server returned "
                    f"{response.status_code}, waiting {wait}s..."
                )
                time.sleep(wait)
                continue
            return response
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as e:
            last_exception = e
            if attempt < MAX_RETRIES:
                wait = BACKOFF_BASE * (2 ** (attempt - 1))
                logger.warning(
                    f"Retry {attempt}/{MAX_RETRIES}: "
                    f"{type(e).__name__}, waiting {wait}s..."
                )
                time.sleep(wait)
            else:
                raise
        except requests.exceptions.RequestException:
            raise
    raise last_exception


def poll_for_available_jobs(
    params: Dict[str, str]
) -> Optional[List[Dict[str, Any]]]:
    """
    Polls the manager's API for available jobs matching the given params.

    Returns:
        A list of job data dictionaries if available, otherwise None.
    """
    poll_url = f"{config.MANAGER_API_URL}jobs/"
    logger.debug(f"Polling for jobs with params: {params}")
    try:
        response = _retry_request(
            requests.get, poll_url,
            params=params, headers=get_auth_headers(), timeout=10
        )
        if handle_auth_response(response):
            return None
        response.raise_for_status()
        available_jobs = response.json()
        if available_jobs:
            return available_jobs
    except requests.exceptions.RequestException as e:
        logger.error(f"Could not poll for jobs: {e}")
    return None


def claim_job(job_id: int, worker_id: int) -> bool:
    """
    Attempts to atomically claim a specific job for this worker.

    Returns:
        True if the job was successfully claimed (HTTP 200).
    """
    claim_url = f"{config.MANAGER_API_URL}jobs/{job_id}/claim/"
    try:
        resp = _retry_request(
            requests.post, claim_url,
            json={"worker_id": worker_id},
            headers=get_auth_headers(), timeout=5
        )
        if handle_auth_response(resp):
            return False
        if resp.status_code == 200:
            return True
        elif resp.status_code == 409:
            logger.warning(
                f"Job {job_id} was claimed by another worker."
            )
        else:
            logger.error(
                f"Failed to claim job {job_id}. "
                f"Status: {resp.status_code}, "
                f"Response: {resp.text}"
            )
    except requests.exceptions.RequestException as e:
        logger.error(
            f"API error while trying to claim job {job_id}: {e}"
        )
    return False


def update_job_status(job_id: int, payload: Dict[str, Any]):
    """Sends a PATCH request to update a job's status."""
    update_url = f"{config.MANAGER_API_URL}jobs/{job_id}/"
    try:
        response = _retry_request(
            requests.patch, update_url,
            json=payload, headers=get_auth_headers(), timeout=5
        )
        if handle_auth_response(response):
            return
        response.raise_for_status()
        logger.debug(
            f"Successfully sent status update for job {job_id}. "
            f"Payload: {payload}"
        )
    except requests.exceptions.RequestException as e:
        logger.error(
            f"Failed to update job status for job {job_id}: {e}"
        )


def upload_render_output(job_id: int, output_file_path: str,
                         thumbnail_path: str = None) -> bool:
    """
    Uploads the rendered output file to the manager's API endpoint.

    On auth failure (401), retains the file in failed_uploads/ so
    rendered output is not lost. Optionally includes a thumbnail PNG
    as a second file field in the multipart request.

    Returns:
        True if the upload was successful, False otherwise.
    """
    if not os.path.exists(output_file_path):
        logger.error(
            f"Render output not found at {output_file_path}. "
            f"Cannot upload."
        )
        return False

    upload_url = f"{config.MANAGER_API_URL}jobs/{job_id}/upload_output/"
    logger.info(f"Uploading render output to {upload_url}...")

    thumb_file = None
    try:
        f = open(output_file_path, 'rb')
        files = {
            'output_file': (
                os.path.basename(output_file_path), f,
                'application/octet-stream'
            )
        }
        # Include thumbnail if available
        if thumbnail_path and os.path.exists(thumbnail_path):
            try:
                thumb_file = open(thumbnail_path, 'rb')
                files['thumbnail'] = (
                    os.path.basename(thumbnail_path),
                    thumb_file, 'image/png'
                )
            except IOError as e:
                logger.warning(
                    f"Could not open thumbnail file "
                    f"{thumbnail_path}: {e}. "
                    f"Uploading without thumbnail."
                )
        response = _retry_request(
            requests.post, upload_url,
            files=files, headers=get_auth_headers(), timeout=60
        )
        if response.status_code == 401:
            handle_auth_response(response)
            retain_failed_upload(job_id, output_file_path)
            return False
        if handle_auth_response(response):
            return False
        response.raise_for_status()
        logger.info(
            f"Successfully uploaded output file for job {job_id}."
        )
        return True
    except requests.exceptions.RequestException as e:
        logger.error(
            f"Failed to upload output file for job {job_id}: {e}"
        )
        return False
    except FileNotFoundError:
        logger.error(
            f"File not found during upload for job {job_id}: "
            f"{output_file_path}"
        )
        return False
    finally:
        if thumb_file:
            thumb_file.close()
        try:
            f.close()
        except Exception:
            pass


def get_job_status(job_id: int) -> Optional[str]:
    """
    Fetch the current status of a job from the manager.

    Performs GET /api/jobs/{job_id}/ with auth headers.

    Returns:
        The job status string (e.g., 'CANCELED'), or None on error.
    """
    job_url = f"{config.MANAGER_API_URL}jobs/{job_id}/"
    try:
        response = _retry_request(
            requests.get, job_url,
            headers=get_auth_headers(), timeout=5
        )
        if handle_auth_response(response):
            return None
        if response.status_code == 200:
            return response.json().get('status')
    except requests.exceptions.RequestException:
        pass
    return None


def send_enrollment_heartbeat(
    payload: Dict[str, Any]
) -> Optional[Dict]:
    """Send an enrollment heartbeat with the X-Enrollment-Key header."""
    return _send_enrollment_heartbeat(_retry_request, payload)


def send_authenticated_heartbeat(
    payload: Dict[str, Any]
) -> Optional[Dict]:
    """Send a heartbeat with the API token."""
    return _send_authenticated_heartbeat(_retry_request, payload)
