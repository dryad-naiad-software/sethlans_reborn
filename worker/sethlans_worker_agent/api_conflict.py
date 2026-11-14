# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Handling of the manager's 409 "job no longer yours" response (issue #217).

The manager rejects a status report or an output upload with HTTP 409
when the job was canceled, requeued or reassigned while this worker was
rendering it.  That is an expected outcome of an admin action, not a
worker failure, so it is logged at INFO and the job is dropped locally.
"""

import logging

logger = logging.getLogger(__name__)

HTTP_CONFLICT = 409


class JobNoLongerAssigned(Exception):
    """The manager no longer accepts results for this job from us."""

    def __init__(self, job_id, reason=''):
        super().__init__(f"Job {job_id} is no longer assigned to this worker. {reason}".strip())
        self.job_id = job_id
        self.reason = reason


def _reason(response):
    try:
        body = response.json()
    except ValueError:
        return ''
    if isinstance(body, dict):
        return str(body.get('error') or body.get('detail') or '')
    return ''


def is_job_conflict(response, job_id, action):
    """Return True (and log at INFO) if ``response`` is the manager's 409.

    Args:
        response: The ``requests`` response.
        job_id: The job the request was for.
        action: What was sent, for the log line (e.g. ``"status update"``).
    """
    if response is None or response.status_code != HTTP_CONFLICT:
        return False
    logger.info(
        "Manager rejected the %s for job %s: the job was canceled, "
        "requeued or reassigned meanwhile. %s",
        action, job_id, _reason(response),
    )
    return True
