# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Manager calls of sequence rendering's worker results (FR-SD-8, FR-SW-10;
FR-SA-6 in a later chunk). Every call has the pilot result's response
handling (``api_extras``): 200 -> ``done``; 409/404 -> ``terminal`` (the
job is no longer this worker's); 401/403 -> ``failed`` at once; 5xx or a
network error -> retried, 3 attempts with backoff, then ``failed``; any
other 4xx -> ``rejected`` with the response's error text. None raises.

* ``report_shots_result(job_id, body)``: ``POST jobs/{id}/shots-result/``.
  The body is at most about 300 KB (2,000 shots of 64-character names, 64
  warnings), well inside the endpoint's 1 MiB class (D21).
* ``upload_frame(job_id, frame, handle, filename)``: ``POST
  jobs/{id}/frames/`` (multipart ``frame``, ``file``) from the handle
  ``fs_safe.open_output_file`` returned: the checked bytes are read once
  (at most 512 MiB, the endpoint's file cap) and every attempt sends them.
* ``report_piece_result(job_id, body)``: ``POST jobs/{id}/piece-result/``.
"""
import logging
import time
from typing import Callable

import requests

from sethlans_worker_agent import api_extras

logger = logging.getLogger(__name__)

SHOTS_RESULT, PIECE_RESULT, FRAMES = 'shots-result', 'piece-result', 'frames'
MAX_FRAME_BYTES = 512 * 1024 * 1024  # the manager's FRAME_FILE_CAP (D21)
FRAME_TIMEOUT_SECONDS = 120
_MIME = {'.png': 'image/png', '.exr': 'image/x-exr'}


def report_shots_result(job_id: int, body: dict,
                        sleep: Callable[[float], None] = time.sleep) -> api_extras.PilotReport:
    return api_extras.post_result(job_id, SHOTS_RESULT, body, sleep)


def report_piece_result(job_id: int, body: dict,
                        sleep: Callable[[float], None] = time.sleep) -> api_extras.PilotReport:
    return api_extras.post_result(job_id, PIECE_RESULT, body, sleep)


def _read(handle):
    """The checked bytes, or a ``PilotReport`` when they cannot be sent."""
    try:
        handle.seek(0)
        data = handle.read(MAX_FRAME_BYTES + 1)
    except OSError as exc:
        return api_extras.PilotReport('rejected', None, f"the frame could not be read ({type(exc).__name__})")
    if len(data) > MAX_FRAME_BYTES:
        return api_extras.PilotReport('rejected', 413, f"the frame is larger than {MAX_FRAME_BYTES} bytes")
    return data


def upload_frame(job_id: int, frame: int, handle, filename: str,
                 sleep: Callable[[float], None] = time.sleep) -> api_extras.PilotReport:
    """FR-SW-10: one kept frame to ``frames/``. Never raises."""
    data = _read(handle)
    if isinstance(data, api_extras.PilotReport):
        return data
    suffix = filename[filename.rfind('.'):].lower() if '.' in filename else ''
    files = {'file': (filename, data, _MIME.get(suffix, 'application/octet-stream'))}
    last = api_extras.PilotReport('failed')
    for attempt in range(api_extras.PILOT_ATTEMPTS):
        try:
            response = api_extras.get_session().request(
                'post', api_extras._url(f"jobs/{int(job_id)}/{FRAMES}/"), data={'frame': str(int(frame))},
                files=files, headers=api_extras.get_auth_headers(), timeout=FRAME_TIMEOUT_SECONDS)
            report = api_extras._classify(response)
            if report is not None:
                return report
            last = api_extras.PilotReport('failed', response.status_code, api_extras._detail(response))
        except requests.exceptions.RequestException as exc:
            last = api_extras.PilotReport('failed', None, f"{type(exc).__name__}: {exc}"[:api_extras.MAX_DETAIL])
        if attempt + 1 < api_extras.PILOT_ATTEMPTS:
            logger.warning("frames/ %s of job %s failed (%s); retrying.", frame, job_id, last.detail or last.status_code)
            sleep(api_extras.PILOT_BACKOFF_SECONDS[min(attempt, len(api_extras.PILOT_BACKOFF_SECONDS) - 1)])
    return last
