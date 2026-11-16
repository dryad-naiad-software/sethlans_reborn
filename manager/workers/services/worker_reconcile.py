# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Requeue jobs a worker is no longer running (Unreal foundation FR-RT-12,
FR-RT-17, D4).

* :func:`reconcile_worker_restart` runs when a registration carries a new
  ``worker_boot_id``: the worker process restarted (hard kill, crash, a
  launcher quit) and runs none of the jobs still assigned to it.
* :func:`reconcile_heartbeat` runs on every periodic heartbeat. It calls
  :func:`reconcile_live` only when the heartbeat carries
  ``active_job_ids`` and a ``worker_boot_id`` equal to ``Worker.boot_id``
  (the process that registered last): a claim whose first POST committed
  but whose acknowledgement was lost leaves a RENDERING job on a live
  worker that is not running it; neither the reaper nor boot
  reconciliation would fire. A heartbeat from another process with the
  same identity (a cloned install, a second copy started by hand) never
  reconciles: its list says nothing about the other process's jobs (#247).

Only renderers whose strategy has ``requeue_on_worker_restart()`` are
reconciled: Unreal, and Blender since #247 (a worker that restarts within
the reaper's staleness window, for example after losing a GPU, would
otherwise strand its Blender jobs in RENDERING). The worker lists a job in
``active_job_ids`` from the moment its claim succeeds until its report and
upload are done; claims and heartbeats are sent one after the other from
the same loop, so a job that was just claimed is either listed or inside
the grace period.
Both act only on the authenticated caller's own ``Worker`` row (#215).
Each job is locked on its own row (#216), re-checked (still RENDERING,
still assigned to this worker, and for live reconciliation still past the
grace period) and passed to ``requeue_or_fail``, so a job the reaper
requeued and another worker (or this one, again) claimed meanwhile is
never touched. A job whose requeue fails is logged and skipped; the
others are still reconciled.
"""

import logging
import threading
import time
from datetime import timedelta

from django.db import DatabaseError, transaction
from django.utils import timezone

from ..constants import LIVE_RECONCILE_GRACE_SECONDS, Renderer
from ..models import Job, JobStatus, Worker
from ..parent_status import take_write_lock
from ..renderers import get_strategy
from ..views.stuck_jobs import requeue_or_fail
from .row_locks import lock_own_rows

logger = logging.getLogger(__name__)

# ``{label}`` is the job's renderer label ('Unreal', 'Blender').
RESTART_MESSAGE = "Auto-requeued: worker restarted during {label} render"
LIVE_MESSAGE = "Auto-requeued: worker is not running this job"

# At most one shared-identity warning per worker in this interval.
IDENTITY_WARNING_INTERVAL_SECONDS = 600
_identity_warned_at = {}  # Worker pk -> time.monotonic() of the last warning
_identity_warned_lock = threading.Lock()
_clock = time.monotonic  # module attribute so tests can move time


def _restart_message(job):
    return RESTART_MESSAGE.format(label=get_strategy(job.renderer).label)


def _live_message(_job):
    return LIVE_MESSAGE


class ReconcileError(Exception):
    """Boot reconciliation failed; the previous ``boot_id`` was restored."""


def _reconciled_renderers():
    return [r for r in Renderer.values if get_strategy(r).requeue_on_worker_restart()]


def _rendering_jobs(worker, **filters):
    """``(pk, name, renderer)`` of ``worker``'s RENDERING jobs of the reconciled renderers."""
    return list(Job.objects.filter(
        assigned_worker=worker, status=JobStatus.RENDERING,
        renderer__in=_reconciled_renderers(), **filters,
    ).values_list('pk', 'name', 'renderer'))


def _started_too_late(job, started_before, null_eligible):
    """True when ``job`` (re)started at or after ``started_before``."""
    if job.started_at is None:
        return not null_eligible
    return job.started_at >= started_before


def _requeue_if_still_owned(worker, job_pk, message_for, started_before, null_eligible):
    """Lock job ``job_pk``, re-check it and requeue it; return whether it was.

    The re-check under the lock: still RENDERING, still assigned to
    ``worker``, and started before ``started_before`` (a job claimed again
    since the selection, by this worker or another, is left alone).
    ``message_for(job)`` gives the requeue message.
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
        new_status = requeue_or_fail(job, message_for(job))
    logger.info(
        "Reconciled job '%s' (ID: %s) of worker '%s': %s.",
        job.name, job.pk, worker.hostname, new_status,
    )
    return True


def _requeue_each(worker, jobs, path, message_for, started_before, null_eligible):
    """Requeue each job in its own transaction; return ``(requeued, skipped)``.

    ``jobs`` holds ``(pk, name, renderer)``; ``path`` is ``'boot'`` or
    ``'live'``. Like the stuck-job reaper, an error on one job is logged
    and the loop goes on, so one bad row cannot keep the others (or a
    registration) from being reconciled; that job's transaction is rolled
    back. A ``DatabaseError`` (lock timeout, deadlock) is expected under
    contention and logged as a WARNING; anything else is a bug and logged
    at ERROR with its traceback.
    """
    requeued = skipped = 0
    for pk, name, renderer in jobs:
        try:
            requeued += _requeue_if_still_owned(worker, pk, message_for, started_before, null_eligible)
        except DatabaseError:
            skipped += 1
            logger.warning(
                "%s reconciliation of job %s ('%s', %s) of worker '%s' hit a database "
                "error; skipped.", path.capitalize(), pk, name, renderer, worker.hostname,
                exc_info=True,
            )
        except Exception:  # noqa: BLE001 - one job must not stop the rest
            skipped += 1
            logger.exception(
                "%s reconciliation of job %s ('%s', %s) of worker '%s' failed; skipped.",
                path.capitalize(), pk, name, renderer, worker.hostname,
            )
    return requeued, skipped


def reconcile_worker_restart(worker, boot_id):
    """Requeue the restarted ``worker``'s RENDERING jobs once per ``boot_id``.

    The reconciliation first claims itself with a compare-and-swap on
    ``Worker.boot_id``, so two concurrent registrations with the same new
    id (a retried POST) reconcile once.  ``worker.boot_id`` must still hold
    the value loaded before this registration.

    A job whose requeue raises is skipped and the registration still
    succeeds with the new ``boot_id``: the new process's heartbeats then
    reconcile it through the live path (it is not in their list).

    Returns:
        The number of jobs returned to the queue or set to ERROR (the
        auto-requeue limit).

    Raises:
        ReconcileError: listing the jobs failed; the previous ``boot_id``
            was restored so a retried registration reconciles again.
    """
    previous = worker.boot_id
    # Jobs claimed from now on belong to the new process: never requeue them.
    cutoff = timezone.now()
    claimed = Worker.objects.filter(pk=worker.pk).exclude(boot_id=boot_id).update(boot_id=boot_id)
    if claimed != 1:
        return 0
    worker.boot_id = boot_id
    try:
        jobs = _rendering_jobs(worker)
    except Exception as exc:
        Worker.objects.filter(pk=worker.pk, boot_id=boot_id).update(boot_id=previous)
        worker.boot_id = previous
        logger.exception("Boot reconciliation of worker '%s' failed.", worker.hostname)
        raise ReconcileError(str(exc)) from exc
    count, skipped = _requeue_each(worker, jobs, 'boot', _restart_message, cutoff, null_eligible=True)
    if count or skipped:
        logger.warning(
            "Worker '%s' restarted: reconciled %d job(s) it was rendering; "
            "%d left to live reconciliation after an error.",
            worker.hostname, count, skipped,
        )
    return count


def reconcile_live(worker, active_ids, now=None):
    """Requeue RENDERING jobs of ``worker`` it no longer reports running.

    A job is reconciled when its id is not in ``active_ids`` and it
    started more than ``LIVE_RECONCILE_GRACE_SECONDS`` before
    ``now``, the arrival of the heartbeat that reported ``active_ids``
    (the caller passes ``worker.last_seen``), so a slow request cannot
    stretch the window over a job claimed after the heartbeat was sent.

    Returns:
        The number of jobs returned to the queue or set to ERROR (the
        auto-requeue limit).
    """
    now = now or timezone.now()
    started_before = now - timedelta(seconds=LIVE_RECONCILE_GRACE_SECONDS)
    listed = set(active_ids)
    jobs = [
        job for job in _rendering_jobs(worker, started_at__lt=started_before)
        if job[0] not in listed
    ]
    count, _skipped = _requeue_each(worker, jobs, 'live', _live_message, started_before, null_eligible=False)
    return count


def _warn_shared_identity(worker):
    """Log the shared-identity WARNING at most once per interval per worker."""
    now = _clock()
    with _identity_warned_lock:
        last = _identity_warned_at.get(worker.pk)
        if last is not None and now - last < IDENTITY_WARNING_INTERVAL_SECONDS:
            return
        _identity_warned_at[worker.pk] = now
    logger.warning(
        "Worker '%s': a heartbeat came from a process other than the one that "
        "registered last. Two worker processes are using the same identity "
        "(token). Live reconciliation is skipped only for the heartbeats of the "
        "process that did not register last; the process that registered last "
        "still reconciles, and requeues the other process's jobs once they are "
        "past the %d s grace period.",
        worker.hostname, LIVE_RECONCILE_GRACE_SECONDS,
    )


def reconcile_heartbeat(worker, active_ids, boot_id, now=None):
    """Live-reconcile a periodic heartbeat from the worker's current process.

    ``active_ids`` and ``boot_id`` are the validated heartbeat values
    (``None`` when absent or invalid). Nothing runs without both. A
    ``boot_id`` other than ``Worker.boot_id`` means another process uses
    this identity: no reconciliation, and a rate-limited WARNING (none
    while the row has no boot id yet).

    Returns:
        The number of jobs returned to the queue or set to ERROR (the
        auto-requeue limit).
    """
    if boot_id is None:
        return 0
    if boot_id != worker.boot_id:
        if worker.boot_id:
            _warn_shared_identity(worker)
        return 0
    if active_ids is None:
        return 0
    return reconcile_live(worker, active_ids, now=now)
