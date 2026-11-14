# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Manager calls the Unreal executor needs (Unreal foundation FR-UE-3,
FR-UE-16, FR-UE-17, FR-UE-25, FR-RT-11).

``api_handler`` is frozen (spec "Files explicitly NOT touched"); this module
reuses its base URL, auth headers, pinned session and retry helper by import.

* ``get_job_assignment(job_id)`` -> ``(status, assigned_worker_id)`` or None
  (the cancel watcher; any failure is None and ignored by the caller).
* ``release_job(job_id, reason)``: ``POST jobs/{id}/release/``, **one attempt,
  5 s timeout, no retry** (FR-UE-17); True on 200. Never raises.
* ``upload_output_handle(job_id, handle, filename)``: uploads from the handle
  ``fs_safe.open_output_file`` returned (FR-UE-16): the checked bytes are read
  once and every retry sends them, so the bytes uploaded are the bytes
  checked. Raises ``JobNoLongerAssigned`` on 409 (#217).
* ``report_pilot_result(job_id, body)`` (FR-UE-25 "Report"): 200 -> ``done``;
  409/404 -> ``terminal``; 401/403 -> ``failed`` at once (an auth problem, not
  a rejected body); 5xx or a network error -> retried, 3 attempts with
  backoff, then ``failed``; any other 4xx -> ``rejected`` with the response's
  error text (at most 500 characters).
"""
import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

import requests

from sethlans_worker_agent import config
from sethlans_worker_agent.api_auth import get_auth_headers, handle_auth_response
from sethlans_worker_agent.api_conflict import JobNoLongerAssigned, is_job_conflict
from sethlans_worker_agent.tls_adapter import get_session

logger = logging.getLogger(__name__)

RELEASE_TIMEOUT_SECONDS = 5
ASSIGNMENT_TIMEOUT_SECONDS = 5
PILOT_ATTEMPTS = 3
PILOT_BACKOFF_SECONDS = (2.0, 8.0)
PILOT_TIMEOUT_SECONDS = 15
MAX_DETAIL = 500
MAX_UPLOAD_BYTES = 1024 * 1024 * 1024  # a 1080p PNG is a few MB; bounds the in-memory copy


def _url(path: str) -> str:
    return f"{config.MANAGER_API_URL}{path}"


def get_job_assignment(job_id: int) -> Optional[Tuple[str, Optional[int]]]:
    """``(status, assigned_worker_id)`` of a job, or None on any failure."""
    try:
        response = get_session().request('get', _url(f"jobs/{int(job_id)}/"), headers=get_auth_headers(),
                                         timeout=ASSIGNMENT_TIMEOUT_SECONDS)
        if handle_auth_response(response) or response.status_code != 200:
            return None
        data = response.json()
        status = data.get('status')
        worker = data.get('assigned_worker')
        if not isinstance(status, str):
            return None
        return status, (worker if isinstance(worker, int) and not isinstance(worker, bool) else None)
    except (requests.exceptions.RequestException, ValueError, AttributeError):
        return None


def release_job(job_id: int, reason: str) -> bool:
    """FR-RT-11 ``release/``: one attempt, 5 s, no retry. Never raises."""
    try:
        response = get_session().request('post', _url(f"jobs/{int(job_id)}/release/"), json={'reason': reason},
                                         headers=get_auth_headers(), timeout=RELEASE_TIMEOUT_SECONDS)
    except requests.exceptions.RequestException as exc:
        logger.warning("Could not release job %s (%s): %s", job_id, reason, exc)
        return False
    if response.status_code == 200:
        logger.info("Released job %s to the manager (%s).", job_id, reason)
        return True
    logger.warning("The manager refused to release job %s (%s): HTTP %s", job_id, reason, response.status_code)
    return False


def upload_output_handle(job_id: int, handle, filename: str) -> bool:
    """Upload the render output from an open handle (FR-UE-16). Raises ``JobNoLongerAssigned``."""
    from sethlans_worker_agent.api_handler import _retry_request
    try:
        # Read the checked bytes once: a file object would be consumed by the first attempt, and the
        # retry helper would send an empty body on a 5xx retry (review M1/W2).
        handle.seek(0)
        data = handle.read(MAX_UPLOAD_BYTES + 1)
    except OSError as exc:
        logger.error("Could not read the output of job %s: %s", job_id, exc)
        return False
    if len(data) > MAX_UPLOAD_BYTES:
        logger.error("The output of job %s is larger than %d bytes; not uploaded.", job_id, MAX_UPLOAD_BYTES)
        return False
    try:
        response = _retry_request('post', _url(f"jobs/{int(job_id)}/upload_output/"),
                                  files={'output_file': (filename, data, 'application/octet-stream')},
                                  headers=get_auth_headers(), timeout=60)
    except requests.exceptions.RequestException as exc:
        logger.error("Failed to upload the output of job %s: %s", job_id, exc)
        return False
    if handle_auth_response(response):
        return False
    if is_job_conflict(response, job_id, "output upload"):
        raise JobNoLongerAssigned(job_id)
    if response.status_code >= 400:
        logger.error("Upload of job %s's output failed: HTTP %s", job_id, response.status_code)
        return False
    logger.info("Uploaded the output of job %s.", job_id)
    return True


@dataclass(frozen=True)
class PilotReport:
    outcome: str        # 'done' | 'terminal' | 'failed' | 'rejected'
    status_code: Optional[int] = None
    detail: str = ''


def _detail(response) -> str:
    try:
        data = response.json()
        if isinstance(data, dict):
            text = data.get('error') or data.get('detail') or data
        else:
            text = data
    except ValueError:
        text = response.text
    return str(text)[:MAX_DETAIL]


def _classify(response) -> Optional[PilotReport]:
    code = response.status_code
    if code == 200:
        return PilotReport('done', code)
    if code in (401, 403):
        # An auth failure is about this worker's token, not the body: never PATCH ERROR for it
        # (the job stays RENDERING and live reconciliation requeues it; review L1).
        handle_auth_response(response)
        return PilotReport('failed', code, 'authentication failed')
    if code in (404, 409):
        return PilotReport('terminal', code, _detail(response))
    if 400 <= code < 500:
        return PilotReport('rejected', code, _detail(response))
    return None  # 5xx: retried


def report_pilot_result(job_id: int, body: dict, sleep: Callable[[float], None] = time.sleep) -> PilotReport:
    """``POST jobs/{id}/pilot-result/`` with the FR-UE-25 response handling. Never raises."""
    last = PilotReport('failed')
    for attempt in range(PILOT_ATTEMPTS):
        try:
            response = get_session().request('post', _url(f"jobs/{int(job_id)}/pilot-result/"), json=body,
                                             headers=get_auth_headers(), timeout=PILOT_TIMEOUT_SECONDS)
            result = _classify(response)
            if result is not None:
                return result
            last = PilotReport('failed', response.status_code, _detail(response))
        except requests.exceptions.RequestException as exc:
            last = PilotReport('failed', None, f"{type(exc).__name__}: {exc}"[:MAX_DETAIL])
        if attempt + 1 < PILOT_ATTEMPTS:
            logger.warning("pilot-result/ for job %s failed (%s); retrying.", job_id, last.detail or last.status_code)
            sleep(PILOT_BACKOFF_SECONDS[min(attempt, len(PILOT_BACKOFF_SECONDS) - 1)])
    return last
