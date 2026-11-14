# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Stuck job detection and automatic requeue logic.

Called from heartbeat processing to detect RENDERING jobs assigned to
offline workers and either requeue them or mark them as ERROR after
exceeding the maximum auto-requeue attempts.
"""

import logging
import threading
import time
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from ..constants import WORKER_STALENESS_SECONDS
from ..models import Job, JobStatus
from ..services.row_locks import lock_own_rows

logger = logging.getLogger(__name__)

_MAX_AUTO_REQUEUE = 3
_STUCK_CHECK_INTERVAL = 30  # seconds
_last_stuck_check = 0.0  # module-level monotonic timestamp
# Phase 4: guard ``_last_stuck_check`` for threaded Waitress.  Two
# concurrent heartbeats could otherwise both observe an old timestamp,
# both pass the debounce gate, and both run the (expensive) stuck-job
# scan in parallel.  A single ``threading.Lock`` is sufficient — the
# check fires at most once per ``_STUCK_CHECK_INTERVAL`` seconds so lock
# contention is negligible.
_last_stuck_check_lock = threading.Lock()

_REAPER_MESSAGE = 'Auto-requeued: assigned worker went offline'


def requeue_or_fail(job, requeue_message):
    """Requeue *job*, or mark it ERROR once it exceeds the requeue limit.

    Shared by the stuck-job reaper and the renderer requeue paths (spec
    FR-RT-13).  Call on a row the caller locked inside its own
    ``transaction.atomic()`` block, after re-checking it.

    * ``auto_requeue_count`` is incremented.
    * Above ``_MAX_AUTO_REQUEUE``: ERROR, ``completed_at`` set and the
      ``"Max auto-requeue attempts exceeded (3)"`` message.
    * Otherwise QUEUED, with ``assigned_worker``, ``started_at`` and
      ``completed_at`` cleared and ``error_message = requeue_message``.
      ``last_output`` is kept.

    Returns:
        The job's new status (``JobStatus.QUEUED`` or ``JobStatus.ERROR``).

    Raises:
        RuntimeError: called outside an atomic block (the row cannot be
            locked there).
    """
    # The job's own connection: the row lock lives there, not on 'default'
    # if the job was loaded through another alias.
    using = getattr(job._state, 'db', None) or 'default'
    if not transaction.get_connection(using=using).in_atomic_block:
        raise RuntimeError("requeue_or_fail must run inside transaction.atomic()")
    job.auto_requeue_count += 1
    if job.auto_requeue_count > _MAX_AUTO_REQUEUE:
        job.status = JobStatus.ERROR
        job.completed_at = timezone.now()
        job.error_message = (
            f'Max auto-requeue attempts exceeded '
            f'({_MAX_AUTO_REQUEUE})'
        )
        job.save(update_fields=[
            'status', 'completed_at', 'error_message',
            'auto_requeue_count',
        ])
    else:
        job.status = JobStatus.QUEUED
        job.assigned_worker = None
        job.started_at = None
        job.completed_at = None
        job.error_message = requeue_message
        job.save(update_fields=[
            'status', 'assigned_worker', 'started_at',
            'completed_at', 'error_message',
            'auto_requeue_count',
        ])
    return job.status


def requeue_stuck_jobs():
    """Requeue RENDERING jobs assigned to offline workers.

    Debounced: runs at most once per _STUCK_CHECK_INTERVAL seconds.
    Uses select_for_update per job row with re-checks after locking
    to prevent races with concurrent job completion or worker recovery.

    Returns:
        Number of jobs requeued or marked as ERROR.
    """
    global _last_stuck_check
    now_mono = time.monotonic()
    # Acquire the debounce lock for the check-and-swap.  Two threads
    # cannot both observe a stale ``_last_stuck_check`` and then each
    # update it — exactly one wins the gate per interval.
    with _last_stuck_check_lock:
        if now_mono - _last_stuck_check < _STUCK_CHECK_INTERVAL:
            return 0
        _last_stuck_check = now_mono

    stale_threshold = timezone.now() - timedelta(seconds=WORKER_STALENESS_SECONDS)
    stuck_jobs = Job.objects.filter(
        status=JobStatus.RENDERING,
        assigned_worker__last_seen__lt=stale_threshold,
    ).select_related('assigned_worker')

    count = 0
    for job_snapshot in stuck_jobs:
        job_pk = job_snapshot.pk
        try:
            with transaction.atomic():
                # Lock only the job row: assigned_worker is nullable, and
                # PostgreSQL rejects FOR UPDATE on an outer join (#216).
                job = lock_own_rows(
                    Job.objects, 'assigned_worker',
                ).get(pk=job_pk)

                # Re-check: job may have completed between filter and lock
                if job.status != JobStatus.RENDERING:
                    continue

                # Re-check: worker may have recovered
                if (job.assigned_worker
                        and job.assigned_worker.last_seen >= stale_threshold):
                    continue

                worker_hostname = (
                    job.assigned_worker.hostname
                    if job.assigned_worker else 'unknown'
                )
                if requeue_or_fail(job, _REAPER_MESSAGE) == JobStatus.ERROR:
                    logger.info(
                        "Stuck job '%s' (ID: %s) exceeded max "
                        "auto-requeue (%d). Set to ERROR. "
                        "Worker: '%s'.",
                        job.name, job.pk,
                        _MAX_AUTO_REQUEUE, worker_hostname,
                    )
                else:
                    logger.info(
                        "Auto-requeued stuck job '%s' (ID: %s) "
                        "from offline worker '%s' "
                        "(attempt %d/%d).",
                        job.name, job.pk, worker_hostname,
                        job.auto_requeue_count, _MAX_AUTO_REQUEUE,
                    )
                count += 1
        except Job.DoesNotExist:
            continue
    return count
