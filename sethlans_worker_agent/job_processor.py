# sethlans_worker_agent/job_processor.py

# ... (Your existing header) ...

import requests
import json
import datetime
import time
import subprocess
import os

from . import config
from . import system_monitor
from .tool_manager import tool_manager_instance

import logging  # <-- NEW IMPORT

logger = logging.getLogger(__name__)  # <-- Get a logger for this module


# --- Job Processing Functions ---

def execute_blender_job(job_data):
    """
    Executes a Blender render job using subprocess.
    Returns (success: bool, stdout: str, stderr: str, error_message: str)
    """
    job_name = job_data.get('name', 'Unnamed Job')
    blend_file_path = job_data.get('blend_file_path')
    output_file_pattern = job_data.get('output_file_pattern')
    start_frame = job_data.get('start_frame', 1)
    end_frame = job_data.get('end_frame', 1)
    blender_version_req = job_data.get('blender_version')
    render_engine = job_data.get('render_engine', 'CYCLES')

    logger.info(f"Starting render for job '{job_name}'...")  # <-- Changed print to logger.info
    logger.debug(f"  Blend File: {blend_file_path}")  # <-- Changed print to logger.debug
    logger.debug(f"  Output Pattern: {output_file_pattern}")
    logger.debug(f"  Frames: {start_frame}-{end_frame}")
    logger.debug(f"  Engine: {render_engine}")
    if blender_version_req:
        logger.debug(f"  Requested Blender Version: {blender_version_req}")

    # --- Determine which Blender executable to use ---
    blender_to_use = config.SYSTEM_BLENDER_EXECUTABLE
    if blender_version_req:
        logger.info(
            f"Attempting to ensure Blender version {blender_version_req} is available...")  # <-- Changed print to logger.info
        managed_blender_path = tool_manager_instance.ensure_blender_version_available(blender_version_req)
        if managed_blender_path:
            blender_to_use = managed_blender_path
            logger.info(f"Using managed Blender version from: {blender_to_use}")  # <-- Changed print to logger.info
        else:
            logger.warning(
                f"Requested Blender version {blender_version_req} not available/downloadable via management system.")  # <-- Changed print to logger.warning
            if not config.SYSTEM_BLENDER_EXECUTABLE:
                error_message = f"Requested Blender version {blender_version_req} not available, and no system fallback defined. Aborting render for job '{job_name}'."
                logger.error(error_message)  # <-- Changed print to logger.error
                return False, "", "", error_message
            else:
                blender_to_use = config.SYSTEM_BLENDER_EXECUTABLE
                logger.info(
                    f"Falling back to default system Blender: {blender_to_use}")  # <-- Changed print to logger.info
    elif not config.SYSTEM_BLENDER_EXECUTABLE:
        error_message = f"No Blender version requested and no system fallback defined. Aborting render for job '{job_name}'."
        logger.error(error_message)  # <-- Changed print to logger.error
        return False, "", "", error_message

    if not blender_to_use:
        error_message = f"Failed to determine any Blender executable path. Aborting render for job '{job_name}'."
        logger.error(error_message)  # <-- Changed print to logger.error
        return False, "", "", error_message

    # Ensure output directory exists before rendering
    output_dir = os.path.dirname(output_file_pattern)
    if not os.path.exists(output_dir):
        try:
            os.makedirs(output_dir)
            logger.info(f"Created output directory: {output_dir}")  # <-- Changed print to logger.info
        except OSError as e:
            err_msg = f"Failed to create output directory {output_dir}: {e}"
            logger.error(f"Failed to create output directory: {err_msg}")  # <-- Changed print to logger.error
            return False, "", "", err_msg

    command = [
        blender_to_use,
        "-b",
        blend_file_path,
        "-o", output_file_pattern.replace('####', '#'),
        "-F", "PNG",
        "-E", render_engine,
    ]

    if start_frame == end_frame:
        command.extend(["-f", str(start_frame)])
    else:
        command.extend(["-s", str(start_frame), "-e", str(end_frame), "-a"])

    logger.info(f"Running Blender command: {' '.join(command)}")  # <-- Changed print to logger.info

    stdout_output = ""
    stderr_output = ""
    error_message = ""
    success = False

    try:
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            cwd=config.PROJECT_ROOT_FOR_WORKER
        )

        stdout_output = process.stdout
        stderr_output = process.stderr

        if process.returncode == 0:
            logger.info("Blender render command exited successfully.")  # <-- Changed print to logger.info
            success = True
        else:
            error_message = f"Blender exited with code {process.returncode}. STDERR: {stderr_output[:500]}..."
            logger.error(f"Blender command failed: {error_message}")  # <-- Changed print to logger.error
            success = False

        logger.debug("--- Blender STDOUT (last 1000 chars) ---")  # <-- Changed print to logger.debug
        logger.debug(stdout_output[-1000:])
        logger.debug("--- Blender STDERR (last 1000 chars) ---")
        logger.debug(stderr_output[-1000:])

    except FileNotFoundError:
        error_message = f"Blender executable not found at '{blender_to_use}'. Please check the path/download."
        logger.error(error_message)  # <-- Changed print to logger.error
    except Exception as e:
        error_message = f"An unexpected error occurred during Blender execution: {e}"
        logger.error(error_message)  # <-- Changed print to logger.error

    return success, stdout_output, stderr_output, error_message


def get_and_claim_job():
    """Polls the manager for available jobs and attempts to claim one. If claimed, executes the job."""
    if not system_monitor.WORKER_INFO.get('id'):
        logger.info("Worker ID not yet known. Skipping job poll.")  # <-- Changed print to logger.info
        return

    jobs_url = f"{config.MANAGER_API_URL}jobs/"
    try:
        logger.info(f"Polling for jobs from {jobs_url}...")  # <-- Changed print to logger.info
        response = requests.get(jobs_url, params={'status': 'QUEUED', 'assigned_worker__isnull': 'true'}, timeout=10)
        response.raise_for_status()
        available_jobs = response.json()

        if available_jobs:
            logger.info(f"Found {len(available_jobs)} available job(s).")  # <-- Changed print to logger.info
            job_to_claim = available_jobs[0]
            job_id = job_to_claim.get('id')
            job_name = job_to_claim.get('name')

            logger.info(f"Attempting to claim job '{job_name}' (ID: {job_id})...")  # <-- Changed print to logger.info

            claim_url = f"{jobs_url}{job_id}/"
            claim_payload = {
                "status": "RENDERING",
                "assigned_worker": system_monitor.WORKER_INFO['id']
            }
            claim_response = requests.patch(claim_url, json=claim_payload, timeout=5)
            claim_response.raise_for_status()

            claimed_job_data = claim_response.json()
            if claimed_job_data.get('status') == 'RENDERING' and claimed_job_data.get('assigned_worker') == \
                    system_monitor.WORKER_INFO['id']:
                logger.info(
                    f"Successfully claimed job '{job_name}'! Starting render...")  # <-- Changed print to logger.info

                success, stdout, stderr, blender_error_msg = execute_blender_job(job_to_claim)

                job_update_payload = {
                    "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z'),
                    "last_output": stdout,
                    "error_message": blender_error_msg if blender_error_msg else stderr,
                }

                if success:
                    job_update_payload["status"] = "DONE"
                else:
                    job_update_payload["status"] = "ERROR"

                try:
                    logger.info(
                        f"Reporting job '{job_name}' status '{job_update_payload['status']}' back to manager...")  # <-- Changed print to logger.info
                    report_response = requests.patch(claim_url, json=job_update_payload, timeout=5)
                    report_response.raise_for_status()
                    logger.info(f"Job '{job_name}' status report successful.")  # <-- Changed print to logger.info
                except requests.exceptions.RequestException as e:
                    logger.error(
                        f"Failed to report job status for '{job_name}' - {e}")  # <-- Changed print to logger.error

                return job_to_claim
            else:
                logger.error(
                    f"Claim failed or manager response was unexpected. Job status: {claimed_job_data.get('status')}. Worker assigned: {claimed_job_data.get('assigned_worker')}")  # <-- Changed print to logger.error
        else:
            logger.info("No QUEUED jobs available.")  # <-- Changed print to logger.info

    except requests.exceptions.Timeout:
        logger.error("Job polling or claiming timed out.")  # <-- Changed print to logger.error
    except requests.exceptions.RequestException as e:
        logger.error(f"Job polling or claiming failed - {e}")  # <-- Changed print to logger.error
    except json.JSONDecodeError:
        logger.error("Failed to decode JSON response from job API.")  # <-- Changed print to logger.error
    except Exception as e:
        logger.error(
            f"An unexpected error occurred during job polling/claiming: {e}")  # <-- Changed print to logger.error