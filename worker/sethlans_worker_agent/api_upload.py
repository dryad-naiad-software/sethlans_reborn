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
            'post', upload_url,
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
