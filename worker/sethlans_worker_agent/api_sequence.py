# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Manager calls of sequence rendering's worker results (FR-SD-8; FR-SW-10,
FR-SA-6 in later chunks).

* ``report_shots_result(job_id, body)``: ``POST jobs/{id}/shots-result/``
  with the pilot result's response handling (``api_extras.post_result``):
  200 -> ``done``; 409/404 -> ``terminal``; 401/403 -> ``failed`` at once;
  5xx or a network error -> retried, 3 attempts with backoff, then
  ``failed``; any other 4xx -> ``rejected`` with the response's error text.
  Never raises. The body is at most about 300 KB (2,000 shots of 64-character
  names, 64 warnings), well inside the endpoint's 1 MiB class (D21).
"""
import time
from typing import Callable

from sethlans_worker_agent import api_extras

SHOTS_RESULT = 'shots-result'


def report_shots_result(job_id: int, body: dict,
                        sleep: Callable[[float], None] = time.sleep) -> api_extras.PilotReport:
    return api_extras.post_result(job_id, SHOTS_RESULT, body, sleep)
