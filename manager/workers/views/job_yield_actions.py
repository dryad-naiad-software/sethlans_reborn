# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Job yield-requeue action mixin.

Provides the ``POST /api/jobs/{pk}/yield-requeue/`` endpoint that
atomically transitions a RENDERING job back to QUEUED when a worker
yields due to artist return, schedule window, manual override or a
deliberate worker stop (``YieldReason``).

The RENDERING -> QUEUED transition is NOT added to
VALID_STATUS_TRANSITIONS — it is only permitted through this
dedicated endpoint.

Counting (#246, #247): a yield is not a failure, so it does not count
against the auto-requeue limit, except ``worker_hardware_changed``: it
goes through ``requeue_or_fail`` (shared ``auto_requeue_count``, ERROR
above the limit), because a scene that itself resets the GPU driver reads
as a hardware change and would otherwise be handed back for ever.

Old process (#247): a hand-back that carries a ``worker_boot_id`` other
than the worker's current one comes from a process that has since been
replaced; the job may already be the new process's claim, so it gets 409
and nothing changes.
"""

import logging

from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from ..constants import YieldReason
from ..models import Job, JobStatus, Worker
from ..services.worker_job_writes import NOT_ASSIGNED_MESSAGE
from .heartbeat_unreal import _validate_boot_id
from .stuck_jobs import requeue_or_fail

logger = logging.getLogger(__name__)

HARDWARE_CHANGED_MESSAGE = "Auto-requeued: worker hardware changed"


def _get_worker_for_request(request):
    """Return the Worker instance for the authenticated request user.

    Returns None if the request user has no linked worker_profile.
    """
    return getattr(request.user, 'worker_profile', None)


def _reason(raw):
    """The ``YieldReason`` value of ``raw``, else ``'unknown'``."""
    return raw if isinstance(raw, str) and raw in YieldReason.values else 'unknown'


def _conflict(job, worker, boot_id):
    """The 409 body for the locked ``job``, or ``None`` if ``worker`` may yield it.

    ``Worker.boot_id`` is read here, after the job lock: a new process
    registers (new boot id) before it can claim the job again, so a claim
    by it is always seen together with its boot id.
    """
    if boot_id is not None:
        current = Worker.objects.filter(pk=worker.pk).values_list('boot_id', flat=True).first()
        if boot_id != current:
            return {"error": NOT_ASSIGNED_MESSAGE}
    if job.status != JobStatus.RENDERING:
        return {"error": f"Cannot yield-requeue a job in '{job.status}' status."}
    if job.assigned_worker_id != worker.pk:
        return {"error": "Only the assigned worker can yield-requeue a job."}
    return None


def _hand_back(job, reason):
    """Return the locked ``job`` to the queue; return its new status."""
    if reason == YieldReason.WORKER_HARDWARE_CHANGED:
        # Counted, and not a yield_requeue_count: each hand-back is in
        # exactly one counter (auto_requeue_count holds the counted ones).
        return requeue_or_fail(job, HARDWARE_CHANGED_MESSAGE)
    job.status = JobStatus.QUEUED
    job.assigned_worker = None
    job.started_at = None
    job.yield_requeue_count += 1
    job.error_message = f"Interrupted: {reason}"
    job.unreal_memory_report = None
    job.save(update_fields=[
        'status', 'assigned_worker', 'started_at',
        'yield_requeue_count', 'error_message', 'unreal_memory_report',
    ])
    return job.status


class JobYieldActionsMixin:
    """Mixin providing the yield-requeue action for JobViewSet."""

    @extend_schema(tags=['Worker Agent'])
    @action(
        detail=True,
        methods=['post'],
        url_path='yield-requeue',
    )
    def yield_requeue(self, request, pk=None):
        """Atomically yield-requeue a job. Worker ownership verified.

        Uses SELECT FOR UPDATE to lock the job row. Verifies the job
        is in RENDERING status and the requesting worker is the
        assigned_worker. An optional ``worker_boot_id`` must match the
        worker's current one.

        Returns:
            200 ``{"status": "requeued"}``, or ``{"status": "error"}`` when
                a counted hand-back (``worker_hardware_changed``) exceeded
                the auto-requeue limit and the job is now ERROR.
            404 if job not found.
            409 if the boot id is not the worker's current one, the job is
                not RENDERING, or the worker is not the owner.
        """
        worker = _get_worker_for_request(request)
        if worker is None:
            return Response(
                {"error": "No worker profile for this user."},
                status=status.HTTP_403_FORBIDDEN,
            )
        reason = _reason(request.data.get('reason', 'unknown'))
        boot_id = _validate_boot_id(request.data.get('worker_boot_id'))

        try:
            with transaction.atomic():
                job = Job.objects.select_for_update().get(pk=pk)
                conflict = _conflict(job, worker, boot_id)
                if conflict is not None:
                    return Response(conflict, status=status.HTTP_409_CONFLICT)
                new_status = _hand_back(job, reason)
        except Job.DoesNotExist:
            return Response(
                {"error": "Job not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        logger.info(
            "Job '%s' (ID: %s) yield-requeued by worker '%s'. "
            "Reason: %s, now %s, yield_requeue_count: %d, auto_requeue_count: %d",
            job.name, job.pk, worker.hostname, reason, new_status,
            job.yield_requeue_count, job.auto_requeue_count,
        )
        body = {"status": "requeued" if new_status == JobStatus.QUEUED else "error"}
        return Response(body, status=status.HTTP_200_OK)
