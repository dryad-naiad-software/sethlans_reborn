# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Job yield-requeue action mixin.

Provides the ``POST /api/jobs/{pk}/yield-requeue/`` endpoint that
atomically transitions a RENDERING job back to QUEUED when a worker
yields due to artist return, schedule window, or manual override.

The RENDERING -> QUEUED transition is NOT added to
VALID_STATUS_TRANSITIONS — it is only permitted through this
dedicated endpoint.
"""

import logging

from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from ..constants import YieldReason
from ..models import Job, JobStatus

logger = logging.getLogger(__name__)


def _get_worker_for_request(request):
    """Return the Worker instance for the authenticated request user.

    Returns None if the request user has no linked worker_profile.
    """
    return getattr(request.user, 'worker_profile', None)


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
        assigned_worker.

        Returns:
            200 on success.
            404 if job not found.
            409 if job is not RENDERING or worker is not the owner.
        """
        worker = _get_worker_for_request(request)
        if worker is None:
            return Response(
                {"error": "No worker profile for this user."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            with transaction.atomic():
                job = Job.objects.select_for_update().get(pk=pk)

                if job.status != JobStatus.RENDERING:
                    return Response(
                        {"error": (
                            "Cannot yield-requeue a job in "
                            f"'{job.status}' status."
                        )},
                        status=status.HTTP_409_CONFLICT,
                    )

                if job.assigned_worker != worker:
                    return Response(
                        {"error": (
                            "Only the assigned worker can "
                            "yield-requeue a job."
                        )},
                        status=status.HTTP_409_CONFLICT,
                    )

                raw_reason = request.data.get('reason', 'unknown')
                valid_reasons = {c[0] for c in YieldReason.choices}
                reason = (
                    raw_reason if raw_reason in valid_reasons
                    else 'unknown'
                )
                job.status = JobStatus.QUEUED
                job.assigned_worker = None
                job.started_at = None
                job.yield_requeue_count += 1
                job.error_message = f"Interrupted: {reason}"
                job.save(update_fields=[
                    'status', 'assigned_worker', 'started_at',
                    'yield_requeue_count', 'error_message',
                ])

        except Job.DoesNotExist:
            return Response(
                {"error": "Job not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        logger.info(
            "Job '%s' (ID: %s) yield-requeued by worker '%s'. "
            "Reason: %s, yield_requeue_count: %d",
            job.name, job.pk, worker.hostname,
            reason, job.yield_requeue_count,
        )
        return Response(
            {"status": "requeued"},
            status=status.HTTP_200_OK,
        )
