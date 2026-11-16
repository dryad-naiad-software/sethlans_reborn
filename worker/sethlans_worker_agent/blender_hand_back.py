# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Handing a Blender job the worker stopped back to the queue (#246, #247).

For a stop caused by the worker shutting down, the job thread posts
``POST jobs/{id}/yield-requeue/`` with reason ``worker_shutdown`` or
``worker_hardware_changed`` and this process's ``worker_boot_id``. The
manager answers 409 when the job is already gone from this worker (requeued
meanwhile, or a newer worker process registered): that counts as done. A
hardware-change hand-back counts against the job's auto-requeue limit; at
the limit the manager answers 200 with ``{"status": "error"}`` (the job is
set to ERROR), which is logged at WARNING. No
yield event is posted; those are artist-return statistics. A job stopped
because it is no longer ours gets nothing. Any failure sends nothing else:
the job stays RENDERING and the manager's restart reconciliation (or the
stuck-job reaper) requeues it.

Time: one request through ``api_handler._retry_request`` (3 attempts with
1 s + 2 s backoff) with a ``(connect, read)`` timeout of ``HAND_BACK_TIMEOUT``,
so at most 3 x 8 s + 3 s = 27 s. The whole stop path can take longer than
the agent's 30 s shutdown log deadline: up to ~5 s for the tree stop, 10 s
bounded wait, up to 2 x 10 s output-reader joins when the process will not
die, then the hand-back, about 62 s at worst. Nothing is lost by that: job
threads are not daemon threads, so the interpreter waits for them before
the process exits, and the main loop has already stopped heartbeating and
claiming. Whether the manager or the reconciliation requeues the job, it
is rendered again.
"""
import logging

from sethlans_worker_agent import config
from sethlans_worker_agent.blender_stop import WORKER_HARDWARE_CHANGED, WORKER_SHUTDOWN

logger = logging.getLogger(__name__)

HAND_BACK_TIMEOUT = (3, 5)  # (connect, read) seconds per attempt


def _answer_status(response):
    """The ``status`` of the manager's JSON answer, or None."""
    try:
        body = response.json()
    except ValueError:
        return None
    return body.get('status') if isinstance(body, dict) else None


def hand_back(job_id, reason) -> bool:
    """Return a job the worker stopped to the queue; never raises.

    Only for the worker-shutdown reasons. Returns True when the manager took
    it back or it was already gone (409).
    """
    if reason not in (WORKER_SHUTDOWN, WORKER_HARDWARE_CHANGED):
        logger.info("Job %s is no longer assigned to this worker; not reporting it.", job_id)
        return False
    from sethlans_worker_agent import api_handler, boot_id
    url = f"{config.MANAGER_API_URL}jobs/{int(job_id)}/yield-requeue/"
    try:
        response = api_handler._retry_request(
            'post', url, json={'reason': reason, 'worker_boot_id': boot_id.BOOT_ID},
            headers=api_handler.get_auth_headers(), timeout=HAND_BACK_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001 - the reconciliation is the backstop
        logger.warning("Could not hand job %s back (%s): %s; the manager will requeue it.", job_id, reason, exc)
        return False
    if response.status_code == 200:
        if _answer_status(response) == 'error':
            logger.warning("Job %s reached the auto-requeue limit and was set to ERROR (%s).", job_id, reason)
        else:
            logger.info("Handed job %s back to the queue (%s).", job_id, reason)
        return True
    if response.status_code == 409:
        logger.info("Job %s is already gone from this worker; nothing to hand back.", job_id)
        return True
    logger.warning("The manager refused to take job %s back (%s): HTTP %s; it will requeue it.",
                   job_id, reason, response.status_code)
    return False
