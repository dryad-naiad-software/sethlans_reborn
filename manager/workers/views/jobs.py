# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

import logging

from django.db import transaction
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from ..models import Job, JobStatus, QueueSetting
from ..permissions import IsAdmin, IsWorker
from ..serializers import JobSerializer
from .job_pause_actions import JobPauseActionsMixin
from .job_release_actions import JobReleaseActionsMixin
from .job_poll_filters import (
    LIST_PARAMETERS, apply_device_prefs_filter, apply_worker_poll_filters,
)
from .job_worker_actions import JobWorkerActionsMixin
from .job_worker_update import JobWorkerUpdateMixin
from .job_yield_actions import JobYieldActionsMixin

logger = logging.getLogger(__name__)


@extend_schema_view(
    list=extend_schema(tags=['Management UI'], parameters=LIST_PARAMETERS),
    retrieve=extend_schema(tags=['Management UI']),
    create=extend_schema(tags=['Management UI']),
    update=extend_schema(tags=['Management UI']),
    partial_update=extend_schema(tags=['Management UI']),
    destroy=extend_schema(tags=['Management UI']),
    cancel=extend_schema(tags=['Management UI']),
    requeue=extend_schema(tags=['Management UI']),
    pause=extend_schema(tags=['Management UI']),
    unpause=extend_schema(tags=['Management UI']),
    claim=extend_schema(tags=['Worker Agent']),
    upload_output=extend_schema(tags=['Worker Agent']),
    yield_requeue=extend_schema(tags=['Worker Agent']),
)
class JobViewSet(
    JobReleaseActionsMixin,
    JobYieldActionsMixin,
    JobPauseActionsMixin,
    JobWorkerActionsMixin,
    JobWorkerUpdateMixin,
    viewsets.ModelViewSet,
):
    """
    API endpoint that allows render jobs to be viewed or created.

    Workers use this endpoint to poll for new jobs, claim them for rendering,
    and update their status and final output.
    """
    queryset = Job.objects.all()
    serializer_class = JobSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = [
        'status', 'assigned_worker', 'animation', 'asset__project',
        'tiled_job', 'renderer',
    ]
    search_fields = ['name', 'asset__name', 'asset__project__name']
    ordering_fields = ['submitted_at', 'status', 'name']

    def get_permissions(self):
        if self.action in ('claim', 'upload_output', 'yield_requeue', 'release'):
            return [IsWorker()]
        elif self.action in ('list', 'retrieve'):
            return [(IsAdmin | IsWorker)()]
        elif self.action == 'partial_update':
            return [(IsAdmin | IsWorker)()]
        elif self.action in ('pause', 'unpause'):
            return [IsAdmin()]
        else:
            return [IsAdmin()]

    def get_queryset(self):
        """
        Overrides the default queryset to allow filtering by a worker's
        currently free device preferences, renderer and version
        compatibility, and to exclude paused jobs when workers poll.
        A worker poll is ordered oldest first (FR-RT-15); other lists
        keep ``Job.Meta.ordering`` (newest first).
        """
        queryset = super().get_queryset().select_related(
            'blender_version',
            'asset__project__blender_version',
        )

        # A worker poll is identified by the presence of these specific query parameters.
        is_worker_poll = (
            'status' in self.request.query_params
            and 'assigned_worker__isnull' in self.request.query_params
        )

        if is_worker_poll:
            if QueueSetting.get_instance().queue_paused:
                return queryset.none()
            queryset = queryset.filter(is_paused=False)
            queryset = apply_worker_poll_filters(
                queryset, self.request.query_params,
                getattr(self.request.user, 'worker_profile', None),
            )

        return apply_device_prefs_filter(queryset, self.request.query_params)

    def get_object(self):
        """Load the job once per request (``partial_update`` reads it twice)."""
        if getattr(self, '_object', None) is None:
            self._object = super().get_object()
        return self._object

    def partial_update(self, request, *args, **kwargs):
        """Refuse worker keys outside the renderer's allowlist first (FR-RT-14)."""
        if IsWorker().has_permission(request, self):
            self._enforce_worker_patch_allowlist(request, self.get_object())
        return super().partial_update(request, *args, **kwargs)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """
        Cancels a render job, setting its status to 'CANCELED'.

        Uses SELECT FOR UPDATE to prevent a race where a worker completes
        a job between the status check and the status mutation.

        Returns:
            A Response containing the updated job data.
        """
        from workers.serializers import VALID_STATUS_TRANSITIONS

        try:
            with transaction.atomic():
                job = Job.objects.select_for_update().get(pk=pk)

                allowed = VALID_STATUS_TRANSITIONS.get(job.status, [])
                if JobStatus.CANCELED not in allowed:
                    return Response(
                        {"error": f"Cannot cancel a job in '{job.status}' status."},
                        status=status.HTTP_409_CONFLICT,
                    )
                old_status = job.status
                job.status = JobStatus.CANCELED
                job.is_paused = False
                if not job.completed_at:
                    job.completed_at = timezone.now()
                job.save()
        except Job.DoesNotExist:
            return Response(
                {"error": "Job not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        logger.info(
            f"Job '{job.name}' (ID: {job.id}) CANCELED. "
            f"Status: {old_status} -> {job.status}."
        )
        serializer = self.get_serializer(job)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def requeue(self, request, pk=None):
        """
        Requeue a failed or canceled job, resetting it to QUEUED status.

        Uses SELECT FOR UPDATE to prevent races with concurrent worker
        updates. Only jobs in ERROR or CANCELED status can be requeued.

        Returns:
            200 on success with updated job data.
            404 if job not found.
            409 if job status does not allow requeue.
        """
        from workers.serializers import VALID_STATUS_TRANSITIONS

        try:
            with transaction.atomic():
                job = Job.objects.select_for_update().get(pk=pk)

                allowed = VALID_STATUS_TRANSITIONS.get(job.status, [])
                if JobStatus.QUEUED not in allowed:
                    return Response(
                        {"error": f"Cannot requeue a job in '{job.status}' status."},
                        status=status.HTTP_409_CONFLICT,
                    )
                old_status = job.status
                job.status = JobStatus.QUEUED
                job.assigned_worker = None
                job.started_at = None
                job.completed_at = None
                job.error_message = ''
                job.last_output = ''
                job.auto_requeue_count = 0
                job.is_paused = False
                job.save(update_fields=[
                    'status', 'assigned_worker', 'started_at',
                    'completed_at', 'error_message', 'last_output',
                    'auto_requeue_count', 'is_paused',
                ])
        except Job.DoesNotExist:
            return Response(
                {"error": "Job not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        logger.info(
            f"Job '{job.name}' (ID: {job.id}) requeued from {old_status}."
        )
        return Response(
            self.get_serializer(job).data, status=status.HTTP_200_OK,
        )
