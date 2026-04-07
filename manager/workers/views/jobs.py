# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

import logging
import re

from django.db import models, transaction
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from ..constants import RenderDevice
from ..models import Job, JobStatus, QueueSetting
from ..permissions import IsAdmin, IsWorker
from ..serializers import JobSerializer
from .job_pause_actions import JobPauseActionsMixin
from .job_worker_actions import JobWorkerActionsMixin

VERSION_REGEX = re.compile(r'^\d+\.\d+\.\d+$')
MAX_AVAILABLE_VERSIONS = 20

logger = logging.getLogger(__name__)


@extend_schema_view(
    list=extend_schema(tags=['Management UI']),
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
)
class JobViewSet(JobPauseActionsMixin, JobWorkerActionsMixin, viewsets.ModelViewSet):
    """
    API endpoint that allows render jobs to be viewed or created.

    Workers use this endpoint to poll for new jobs, claim them for rendering,
    and update their status and final output.
    """
    queryset = Job.objects.all()
    serializer_class = JobSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'assigned_worker', 'animation', 'asset__project', 'tiled_job']
    search_fields = ['name', 'asset__name', 'asset__project__name']
    ordering_fields = ['submitted_at', 'status', 'name']

    def get_permissions(self):
        if self.action in ('claim', 'upload_output'):
            return [IsWorker()]
        elif self.action in ('list', 'retrieve'):
            return [(IsAdmin | IsWorker)()]
        elif self.action == 'partial_update':
            return [(IsAdmin | IsWorker)()]
        elif self.action in ('pause', 'unpause'):
            return [IsAdmin()]
        else:
            return [IsAdmin()]

    def _check_worker_owns_job(self, request, job):
        """Verify that a worker-authenticated user owns this job."""
        if hasattr(request.user, 'worker_profile'):
            if request.user.worker_profile != job.assigned_worker:
                raise PermissionDenied(
                    "You do not have permission to modify this job."
                )

    def perform_update(self, serializer):
        """Check worker ownership before allowing partial_update."""
        self._check_worker_owns_job(self.request, serializer.instance)
        serializer.save()

    def get_queryset(self):
        """
        Overrides the default queryset to allow filtering by a worker's
        currently free device preferences, Blender version compatibility,
        and to exclude paused jobs when workers poll.
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
            queryset = self._apply_version_filter(queryset)

        queryset = self._apply_device_prefs_filter(queryset)
        return queryset

    def _apply_device_prefs_filter(self, queryset):
        """Filter by device_prefs CSV query param (FR-9a, FR-9b).

        Parses a comma-separated list of RenderDevice values and
        restricts the queryset to jobs whose ``render_device`` is in the
        requested set. Invalid values or an empty parsed list raise a
        ValidationError (HTTP 400); the parameter being absent is a no-op.
        """
        from rest_framework.exceptions import ValidationError

        param = self.request.query_params.get('device_prefs')
        if param is None:
            return queryset

        raw = [v.strip().upper() for v in param.split(',') if v.strip()]
        if not raw:
            raise ValidationError(
                "device_prefs must be a non-empty CSV of "
                f"{sorted(RenderDevice.values)}."
            )
        allowed = set(RenderDevice.values)
        invalid = [v for v in raw if v not in allowed]
        if invalid:
            raise ValidationError(
                f"Invalid device_prefs value(s): {invalid}. "
                f"Allowed: {sorted(allowed)}."
            )
        logger.debug(
            "Filtering jobs by device_prefs=%s.", raw,
        )
        return queryset.filter(render_device__in=raw)

    def _apply_version_filter(self, queryset):
        """Filter by available_versions query param (series-level matching)."""
        from rest_framework.exceptions import ValidationError

        param = self.request.query_params.get('available_versions')
        if not param:
            return queryset

        versions = [v.strip() for v in param.split(',') if v.strip()]
        if len(versions) > MAX_AVAILABLE_VERSIONS:
            raise ValidationError(
                f"Maximum {MAX_AVAILABLE_VERSIONS} versions allowed."
            )
        for v in versions:
            if not VERSION_REGEX.match(v):
                raise ValidationError(f"Invalid version format: {v}")

        # Extract series from full versions for series-level matching
        series_list = list({'.'.join(v.split('.')[:2]) for v in versions})
        return queryset.filter(
            models.Q(blender_version__series__in=series_list)
            | models.Q(
                blender_version__isnull=True,
                asset__project__blender_version__series__in=series_list,
            )
        )

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
