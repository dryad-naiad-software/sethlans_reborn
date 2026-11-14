# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Requeue jobs a worker is no longer running (Unreal foundation FR-RT-12,
FR-RT-17, D4).

* :func:`reconcile_worker_restart` runs when a registration carries a new
  ``worker_boot_id``: the worker process restarted (hard kill, crash, a
  launcher quit) and runs none of the jobs still assigned to it.
* :func:`reconcile_live` runs on every heartbeat that carries
  ``active_job_ids``: a claim whose first POST committed but whose
  acknowledgement was lost leaves a RENDERING job on a live worker that is
  not running it; neither the reaper nor boot reconciliation would fire.

Only renderers whose strategy has ``requeue_on_worker_restart()`` (Unreal)
are reconciled; Blender jobs are left to the stuck-job reaper (OQ-16).
Both act only on the authenticated caller's own ``Worker`` row (#215).
Each job is locked on its own row (#216), re-checked (still RENDERING,
still assigned to this worker, and for live reconciliation still past the
grace period) and passed to ``requeue_or_fail``, so a job the reaper
requeued and another worker (or this one, again) claimed meanwhile is
never touched.
"""

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from ..constants import UNREAL_LIVE_RECONCILE_GRACE_SECONDS, Renderer
from ..models import Job, JobStatus, Worker
from ..parent_status import take_write_lock
from ..renderers import get_strategy
from ..views.stuck_jobs import requeue_or_fail
from .row_locks import lock_own_rows

logger = logging.getLogger(__name__)

RESTART_MESSAGE = "Auto-requeued: worker restarted during Unreal render"
LIVE_MESSAGE = "Auto-requeued: worker is not running this job"


class ReconcileError(Exception):
    """Boot reconciliation failed; the previous ``boot_id`` was restored."""


def _reconciled_renderers():
    return [r for r in Renderer.values if get_strategy(r).requeue_on_worker_restart()]


def _started_too_late(job, started_before, null_eligible):
    """True when ``job`` (re)started at or after ``started_before``."""
    if job.started_at is None:
        return not null_eligible
    return job.started_at >= started_before


def _requeue_if_still_owned(worker, job_pk, message, started_before, null_eligible):
    """Lock job ``job_pk``, re-check it and requeue it; return whether it was.

    The re-check under the lock: still RENDERING, still assigned to
    ``worker``, and started before ``started_before`` (a job claimed again
    since the selection, by this worker or another, is left alone).
    """
    with transaction.atomic():
        # A write first, so SQLite takes its write lock now rather than
        # upgrading a read snapshot later (SQLITE_BUSY_SNAPSHOT).
        take_write_lock(Job.objects.filter(pk=job_pk))
        try:
            job = lock_own_rows(Job.objects).get(pk=job_pk)
        except Job.DoesNotExist:
            return False
        if job.status != JobStatus.RENDERING or job.assigned_worker_id != worker.pk:
            return False
        if _started_too_late(job, started_before, null_eligible):
            return False
        new_status = requeue_or_fail(job, message)
    logger.info(
        "Reconciled job '%s' (ID: %s) of worker '%s': %s.",
        job.name, job.pk, worker.hostname, new_status,
    )
    return True


def reconcile_worker_restart(worker, boot_id):
    """Requeue the restarted ``worker``'s RENDERING jobs once per ``boot_id``.

    The reconciliation first claims itself with a compare-and-swap on
    ``Worker.boot_id``, so two concurrent registrations with the same new
    id (a retried POST) reconcile once.  ``worker.boot_id`` must still hold
    the value loaded before this registration.

    Returns:
        The number of jobs requeued or failed.

    Raises:
        ReconcileError: a step after the compare-and-swap failed; the
            previous ``boot_id`` was restored so a retried registration
            reconciles again.
    """
    previous = worker.boot_id
    # Jobs claimed from now on belong to the new process: never requeue them.
    cutoff = timezone.now()
    claimed = Worker.objects.filter(pk=worker.pk).exclude(boot_id=boot_id).update(boot_id=boot_id)
    if claimed != 1:
        return 0
    worker.boot_id = boot_id
    try:
        job_pks = list(Job.objects.filter(
            assigned_worker=worker, status=JobStatus.RENDERING,
            renderer__in=_reconciled_renderers(),
        ).values_list('pk', flat=True))
        count = sum(
            _requeue_if_still_owned(worker, pk, RESTART_MESSAGE, cutoff, null_eligible=True)
            for pk in job_pks
        )
    except Exception as exc:
        Worker.objects.filter(pk=worker.pk, boot_id=boot_id).update(boot_id=previous)
        worker.boot_id = previous
        logger.exception("Boot reconciliation of worker '%s' failed.", worker.hostname)
        raise ReconcileError(str(exc)) from exc
    if count:
        logger.warning(
            "Worker '%s' restarted: reconciled %d job(s) it was rendering.",
            worker.hostname, count,
        )
    return count


def reconcile_live(worker, active_ids, now=None):
    """Requeue RENDERING jobs of ``worker`` it no longer reports running.

    A job is reconciled when its id is not in ``active_ids`` and it
    started more than ``UNREAL_LIVE_RECONCILE_GRACE_SECONDS`` before
    ``now``, the arrival of the heartbeat that reported ``active_ids``
    (the caller passes ``worker.last_seen``), so a slow request cannot
    stretch the window over a job claimed after the heartbeat was sent.

    Returns:
        The number of jobs requeued or failed.
    """
    now = now or timezone.now()
    started_before = now - timedelta(seconds=UNREAL_LIVE_RECONCILE_GRACE_SECONDS)
    job_pks = list(Job.objects.filter(
        assigned_worker=worker, status=JobStatus.RENDERING,
        renderer__in=_reconciled_renderers(), started_at__lt=started_before,
    ).exclude(pk__in=active_ids).values_list('pk', flat=True))
    return sum(
        _requeue_if_still_owned(worker, pk, LIVE_MESSAGE, started_before, null_eligible=False)
        for pk in job_pks
    )
