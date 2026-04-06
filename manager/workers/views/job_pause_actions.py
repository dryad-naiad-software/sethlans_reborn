# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Job pause/unpause actions.

Extracted from jobs.py to keep file sizes under the 300-line limit.
"""

import logging

from django.db import transaction
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from ..models import Job, JobStatus

logger = logging.getLogger(__name__)


class JobPauseActionsMixin:
    """Mixin providing pause/unpause actions for JobViewSet."""

    @action(detail=True, methods=['post'])
    def pause(self, request, pk=None):
        """
        Pause a QUEUED job so it is skipped during worker polling.

        Uses SELECT FOR UPDATE to prevent races. Only QUEUED, non-paused
        jobs can be paused.

        Returns:
            200 on success with updated job data.
            404 if job not found.
            409 if job status does not allow pause or already paused.
        """
        try:
            with transaction.atomic():
                job = Job.objects.select_for_update().get(pk=pk)
                if job.status != JobStatus.QUEUED:
                    return Response(
                        {"error": "Only QUEUED jobs can be paused."},
                        status=status.HTTP_409_CONFLICT,
                    )
                if job.is_paused:
                    return Response(
                        {"error": "Job is already paused."},
                        status=status.HTTP_409_CONFLICT,
                    )
                job.is_paused = True
                job.save(update_fields=['is_paused'])
        except Job.DoesNotExist:
            return Response(
                {"error": "Job not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        logger.info(
            f"Job '{job.name}' (ID: {job.id}) paused."
        )
        return Response(self.get_serializer(job).data)

    @action(detail=True, methods=['post'])
    def unpause(self, request, pk=None):
        """
        Unpause a paused job so it becomes available for worker polling.

        Uses SELECT FOR UPDATE to prevent races.

        Returns:
            200 on success with updated job data.
            404 if job not found.
            409 if job is not paused.
        """
        try:
            with transaction.atomic():
                job = Job.objects.select_for_update().get(pk=pk)
                if not job.is_paused:
                    return Response(
                        {"error": "Job is not paused."},
                        status=status.HTTP_409_CONFLICT,
                    )
                job.is_paused = False
                job.save(update_fields=['is_paused'])
        except Job.DoesNotExist:
            return Response(
                {"error": "Job not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        logger.info(
            f"Job '{job.name}' (ID: {job.id}) unpaused."
        )
        return Response(self.get_serializer(job).data)
