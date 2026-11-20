# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Settling shot discovery records (Unreal sequence rendering FR-SD-8,
FR-SD-9, D22).

* :func:`plan_waiting_submissions` / :func:`fail_waiting_submissions` -- the
  hooks a finished discovery calls for its DISCOVERING submissions (the
  work is in ``sequence_submit``).
* :func:`settle_discovery` -- a ``shots`` job that ended ERROR, CANCELED,
  DONE without a result, or was deleted, fails its discovery.  It runs
  after commit under ``lock_project`` and is idempotent; reads call it for
  open records whose job is final, and the server start hook (D16) calls
  :func:`settle_open_discoveries`.

Creating records is ``sequence_discovery_create``; storing a result is
``sequence_shots_result``.  Lock order (D22): Project -> Job ->
SequenceDiscovery -> SequenceSubmission.  The status mirror
(:func:`on_shots_job_saved`) runs after the caller's Job lock and writes
the discovery by ``update()``, never ``save()``.
"""

import logging

from django.db import transaction
from django.db.models import Q

from shared.unreal.shots_result import clean_text

from ..constants_sequence import (
    OPEN_DISCOVERY_STATUSES, SHOTS_JOB_CANCELED, SHOTS_JOB_DELETED, SHOTS_JOB_DONE_WITHOUT_RESULT,
    SHOTS_JOB_FAILED, DiscoveryStatus,
)
from ..models import Job, JobStatus, Project, SequenceDiscovery
from .row_locks import lock_own_rows
from .unreal_pilot import lock_project, locked_project

logger = logging.getLogger(__name__)

MAX_REASON = 500
_ACTIVE = (JobStatus.QUEUED, JobStatus.RENDERING)


def plan_waiting_submissions(discovery):
    """FR-SD-8: plan every submission still DISCOVERING on the DONE ``discovery``.

    Runs in ``record_shots_result``'s transaction, under the discovery lock.
    """
    from .sequence_submit import plan_waiting
    plan_waiting(discovery)


def fail_waiting_submissions(discovery, reason):
    """FR-SD-9: set every submission still DISCOVERING on ``discovery`` ERROR (under its lock)."""
    from .sequence_submit import fail_waiting
    fail_waiting(discovery, reason)


def failure_reason(job_row):
    """Why a discovery fails for its job (``{'status', 'error_message'}`` or ``None``), else ``None``."""
    if job_row is None:
        return SHOTS_JOB_DELETED
    status = job_row['status']
    if status == JobStatus.ERROR:
        return clean_text(job_row['error_message'] or '').strip()[:MAX_REASON] or SHOTS_JOB_FAILED
    if status == JobStatus.CANCELED:
        return SHOTS_JOB_CANCELED
    if status == JobStatus.DONE:
        return SHOTS_JOB_DONE_WITHOUT_RESULT
    return None


def _settle_locked(discovery_id):
    """Under the project lock: lock the open discovery and fail it if its job ended."""
    discovery = lock_own_rows(SequenceDiscovery.objects).filter(
        pk=discovery_id, status__in=OPEN_DISCOVERY_STATUSES,
    ).first()
    if discovery is None:
        return False
    job_row = None
    if discovery.job_id is not None:
        job_row = Job.objects.filter(pk=discovery.job_id).values('status', 'error_message').first()
    reason = failure_reason(job_row)
    if reason is None:
        return False
    SequenceDiscovery.objects.filter(pk=discovery.pk).update(status=DiscoveryStatus.ERROR, error_message=reason)
    fail_waiting_submissions(discovery, reason)
    logger.info("Shot discovery %s failed: %s", discovery.pk, reason)
    return True


def settle_discovery(discovery_id):
    """FR-SD-9 in a new transaction under ``lock_project``; True when it failed the record."""
    project_id = SequenceDiscovery.objects.filter(pk=discovery_id).values_list('project_id', flat=True).first()
    if project_id is None:
        return False
    with transaction.atomic():
        try:
            lock_project(Project(pk=project_id))
        except Project.DoesNotExist:
            return False
        return _settle_locked(discovery_id)


def settle_for_job(job_pk):
    """Settle the discovery of a ``shots`` job, if it has one."""
    discovery_id = SequenceDiscovery.objects.filter(job_id=job_pk).values_list('pk', flat=True).first()
    return discovery_id is not None and settle_discovery(discovery_id)


def stale_discoveries(queryset=None):
    """Open discoveries whose job is final or gone (FR-SD-9 recovery)."""
    queryset = SequenceDiscovery.objects.all() if queryset is None else queryset
    return queryset.filter(status__in=OPEN_DISCOVERY_STATUSES).filter(
        Q(job__isnull=True) | ~Q(job__status__in=_ACTIVE),
    )


def settle_open_discoveries(queryset=None):
    """Settle every stale discovery of ``queryset`` (the start hook); returns how many failed."""
    settled = 0
    for discovery_id in list(stale_discoveries(queryset).values_list('pk', flat=True)):
        try:
            settled += bool(settle_discovery(discovery_id))
        except Exception:  # noqa: BLE001 -- one bad row must not stop the others
            logger.exception("Settling shot discovery %s failed.", discovery_id)
    return settled


def on_shots_job_saved(job):
    """``post_save`` of a ``shots`` job: mirror QUEUED/RUNNING; settle ERROR or CANCELED after commit.

    A DONE save queues nothing: ``shots-result/`` stored the result in the
    same transaction, and a DONE without a result is settled by a read.
    """
    if job.status == JobStatus.RENDERING:
        SequenceDiscovery.objects.filter(job_id=job.pk, status=DiscoveryStatus.QUEUED).update(
            status=DiscoveryStatus.RUNNING)
    elif job.status == JobStatus.QUEUED:
        SequenceDiscovery.objects.filter(job_id=job.pk, status=DiscoveryStatus.RUNNING).update(
            status=DiscoveryStatus.QUEUED)
    elif job.status in (JobStatus.ERROR, JobStatus.CANCELED):
        transaction.on_commit(lambda pk=job.pk: settle_for_job(pk), robust=True)


def settle_canceled_shots_jobs(project, canceled_at):
    """Project cancel-all (FR-SS-8): settle the discoveries of the shots jobs it canceled.

    Runs in the cancel's transaction after its job UPDATE, under the
    project lock (D22: Project -> Job rows -> SequenceDiscovery), in pk order.
    """
    locked_project(project)
    ids = SequenceDiscovery.objects.filter(
        job__asset__project=project, job__status=JobStatus.CANCELED, job__completed_at=canceled_at,
        status__in=OPEN_DISCOVERY_STATUSES,
    ).order_by('pk').values_list('pk', flat=True)
    return sum(_settle_locked(pk) for pk in list(ids))


def before_shots_job_delete(job):
    """Call before deleting a locked ``shots`` job: settle its discovery after commit."""
    discovery_id = SequenceDiscovery.objects.filter(job_id=job.pk).values_list('pk', flat=True).first()
    if discovery_id is not None:
        transaction.on_commit(lambda pk=discovery_id: settle_discovery(pk), robust=True)
