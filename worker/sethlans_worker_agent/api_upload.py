# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Render output upload to the manager's API.

Extracted from api_handler.py for file size compliance. Shares the
_retry_request helper and auth handling from api_handler. All requests
are routed through the thread-local pinning session.
"""
import logging
import os

import requests

from sethlans_worker_agent import config
from sethlans_worker_agent.api_conflict import (
    JobNoLongerAssigned, is_job_conflict,
)
from sethlans_worker_agent.api_auth import (
    get_auth_headers,
    handle_auth_response,
    retain_failed_upload,
)

logger = logging.getLogger(__name__)


def upload_render_output(job_id: int, output_file_path: str,
                         thumbnail_path: str = None) -> bool:
    """
    Uploads the rendered output file to the manager's API endpoint.

    On auth failure (401), retains the file in failed_uploads/ so
    rendered output is not lost. Optionally includes a thumbnail PNG
    as a second file field in the multipart request.

    Returns:
        True if the upload was successful, False otherwise.

    Raises:
        JobNoLongerAssigned: the manager answered 409 because the job was
            canceled, requeued or reassigned meanwhile (issue #217).  The
            output is not wanted any more and nothing is retained.
    """
    from sethlans_worker_agent.api_handler import _retry_request

    if not os.path.exists(output_file_path):
        logger.error(
            f"Render output not found at {output_file_path}. "
            f"Cannot upload."
        )
        return False

    upload_url = f"{config.MANAGER_API_URL}jobs/{job_id}/upload_output/"
    logger.info(f"Uploading render output to {upload_url}...")

    try:
        files = _build_upload_files(output_file_path, thumbnail_path)
        response = _retry_request(
            'post', upload_url,
            files=files, headers=get_auth_headers(), timeout=60
        )
        return _accept_upload_response(response, job_id, output_file_path)
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


def _read_bytes(path: str) -> bytes:
    with open(path, 'rb') as handle:
        return handle.read()


def _build_upload_files(output_file_path, thumbnail_path):
    """
    Build the multipart ``files`` dict with each file's bytes (issue #230).

    ``_retry_request`` sends the same kwargs on every attempt. A file object
    would be read to EOF by the first attempt, so a retry after a 5xx or a
    timeout would send empty parts; bytes are sent whole every time.  This
    costs no extra memory: requests reads a file object fully into memory
    while it encodes a multipart body anyway.
    """
    files = {
        'output_file': (
            os.path.basename(output_file_path),
            _read_bytes(output_file_path),
            'application/octet-stream'
        )
    }
    # Include thumbnail if available
    if thumbnail_path and os.path.exists(thumbnail_path):
        try:
            files['thumbnail'] = (
                os.path.basename(thumbnail_path),
                _read_bytes(thumbnail_path), 'image/png'
            )
        except OSError as e:
            logger.warning(
                f"Could not open thumbnail file "
                f"{thumbnail_path}: {e}. "
                f"Uploading without thumbnail."
            )
    return files


def _accept_upload_response(response, job_id, output_file_path):
    """Map the upload response to True/False, or raise on 409 (#217)."""
    if response.status_code == 401:
        handle_auth_response(response)
        retain_failed_upload(job_id, output_file_path)
        return False
    if handle_auth_response(response):
        return False
    if is_job_conflict(response, job_id, "output upload"):
        raise JobNoLongerAssigned(job_id)
    response.raise_for_status()
    logger.info(
        f"Successfully uploaded output file for job {job_id}."
    )
    return True
