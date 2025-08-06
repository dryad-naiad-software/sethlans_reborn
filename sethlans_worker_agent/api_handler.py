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
# Created by Gemini on 8/5/2025.
# Dryad and Naiad Software LLC
#
# Project: sethlans_reborn
#
"""
Handles all direct API communication between the worker agent and the manager.

This module abstracts the underlying `requests` calls into a set of functions
that are specific to the Sethlans Reborn API contract. It is responsible for
polling, claiming, and updating the status of jobs, as well as uploading
render outputs.
"""
import logging
import os
from typing import Optional, Dict, Any, List

import requests

from sethlans_worker_agent import config

logger = logging.getLogger(__name__)


def poll_for_available_jobs(params: Dict[str, str]) -> Optional[List[Dict[str, Any]]]:
    """
    Polls the manager's API for available jobs matching the given parameters.

    Args:
        params (dict): A dictionary of query parameters for filtering jobs
                       (e.g., {'status': 'QUEUED'}).

    Returns:
        A list of job data dictionaries if available, otherwise None. Returns
        None on network errors or if no jobs are available.
    """
    poll_url = f"{config.MANAGER_API_URL}jobs/"
    logger.debug(f"Polling for jobs with params: {params}")
    try:
        response = requests.get(poll_url, params=params, timeout=10)
        response.raise_for_status()
        available_jobs = response.json()
        if available_jobs:
            return available_jobs
    except requests.exceptions.RequestException as e:
        logger.error(f"Could not poll for jobs: {e}")
    return None


def claim_job(job_id: int, worker_id: int) -> bool:
    """
    Attempts to claim a specific job for this worker via a PATCH request.

    Args:
        job_id (int): The ID of the job to claim.
        worker_id (int): The ID of the worker attempting the claim.

    Returns:
        True if the job was successfully claimed (HTTP 200).
        False if the job was claimed by another worker (HTTP 409).
        False for any other error.
    """
    claim_url = f"{config.MANAGER_API_URL}jobs/{job_id}/"
    try:
        claim_response = requests.patch(claim_url, json={"assigned_worker": worker_id}, timeout=5)

        if claim_response.status_code == 200:
            return True
        elif claim_response.status_code == 409:
            logger.warning(f"Job {job_id} was claimed by another worker.")
        else:
            logger.error(
                f"Failed to claim job {job_id}. Status: {claim_response.status_code}, Response: {claim_response.text}")
    except requests.exceptions.RequestException as e:
        logger.error(f"API error while trying to claim job {job_id}: {e}")

    return False


def update_job_status(job_id: int, payload: Dict[str, Any]):
    """
    Sends a PATCH request to the manager to update a job's status or other data.

    Args:
        job_id (int): The ID of the job to update.
        payload (dict): The data to send in the request body.
    """
    update_url = f"{config.MANAGER_API_URL}jobs/{job_id}/"
    try:
        response = requests.patch(update_url, json=payload, timeout=5)
        response.raise_for_status()
        logger.debug(f"Successfully sent status update for job {job_id}. Payload: {payload}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to update job status for job {job_id}: {e}")


def upload_render_output(job_id: int, output_file_path: str) -> bool:
    """
    Uploads the rendered output file to the manager's dedicated API endpoint.

    Args:
        job_id (int): The ID of the job the file belongs to.
        output_file_path (str): The local filesystem path of the file to upload.

    Returns:
        True if the upload was successful, False otherwise.
    """
    if not os.path.exists(output_file_path):
        logger.error(f"Render output file not found at {output_file_path}. Cannot upload.")
        return False

    upload_url = f"{config.MANAGER_API_URL}jobs/{job_id}/upload_output/"
    logger.info(f"Uploading render output {output_file_path} to {upload_url}...")

    try:
        with open(output_file_path, 'rb') as f:
            files = {'output_file': (os.path.basename(output_file_path), f, 'image/png')}
            response = requests.post(upload_url, files=files, timeout=60)
            response.raise_for_status()
        logger.info(f"Successfully uploaded output file for job {job_id}.")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to upload output file for job {job_id}: {e}")
        return False
    except FileNotFoundError:
        logger.error(f"File not found during upload attempt for job {job_id}: {output_file_path}")
        return False