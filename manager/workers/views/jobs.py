# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

import logging
import os
import re

from django.conf import settings as django_settings
from django.db import transaction
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from PIL import Image
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response

from ..constants import RenderDevice
from ..models import Job, JobStatus, Worker
from ..permissions import IsAdmin, IsWorker
from ..serializers import JobSerializer

logger = logging.getLogger(__name__)


@extend_schema_view(
    list=extend_schema(tags=['Management UI']),
    retrieve=extend_schema(tags=['Management UI']),
    create=extend_schema(tags=['Management UI']),
    update=extend_schema(tags=['Management UI']),
    partial_update=extend_schema(tags=['Management UI']),
    destroy=extend_schema(tags=['Management UI']),
    cancel=extend_schema(tags=['Management UI']),
    claim=extend_schema(tags=['Worker Agent']),
    upload_output=extend_schema(tags=['Worker Agent']),
)
class JobViewSet(viewsets.ModelViewSet):
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
        Overrides the default queryset to allow filtering based on worker GPU capability
        and to exclude jobs from paused projects when workers poll for jobs.

        If a worker polls with `gpu_available=true`, only jobs that can use a GPU are returned.
        If a worker polls with `gpu_available=false`, only jobs that can use a CPU are returned.
        If a worker is not polling (i.e., this is a regular API request), all jobs are returned.
        """
        queryset = super().get_queryset()
        gpu_available_param = self.request.query_params.get('gpu_available')

        # A worker poll is identified by the presence of these specific query parameters.
        is_worker_poll = (
            'status' in self.request.query_params
            and 'assigned_worker__isnull' in self.request.query_params
        )

        # Filter out jobs from paused projects ONLY when a worker is polling for available work.
        # This allows direct access to a job's details via its ID even if paused.
        if is_worker_poll:
            queryset = queryset.filter(asset__project__is_paused=False)

        if gpu_available_param == 'true':
            logger.debug("Filtering jobs for a GPU-capable worker. Including GPU and ANY jobs.")
            return queryset.filter(render_device__in=[RenderDevice.GPU, RenderDevice.ANY])
        elif gpu_available_param == 'false':
            logger.debug("Filtering jobs for a CPU-only worker. Including CPU and ANY jobs.")
            return queryset.filter(render_device__in=[RenderDevice.CPU, RenderDevice.ANY])

        return queryset

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """
        Cancels a render job, setting its status to 'CANCELED'.

        Args:
            request: The request object.
            pk: The primary key of the job to cancel.

        Returns:
            A Response containing the updated job data.
        """
        from workers.serializers import VALID_STATUS_TRANSITIONS

        job = self.get_object()
        allowed = VALID_STATUS_TRANSITIONS.get(job.status, [])
        if JobStatus.CANCELED not in allowed:
            return Response(
                {"error": f"Cannot cancel a job in '{job.status}' status."},
                status=status.HTTP_409_CONFLICT,
            )
        old_status = job.status
        job.status = JobStatus.CANCELED
        if not job.completed_at:
            job.completed_at = timezone.now()
        job.save()
        logger.info(f"Job '{job.name}' (ID: {job.id}) CANCELED. Status: {old_status} -> {job.status}.")
        serializer = self.get_serializer(job)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def claim(self, request, pk=None):
        """
        Atomically claim a job for a worker.

        Uses SELECT FOR UPDATE to lock the job row, preventing race
        conditions where multiple workers claim the same job.

        Expects a JSON body with `worker_id` (the Worker primary key).

        Returns:
            200 on success with updated job data.
            400 if worker_id is missing or invalid.
            404 if job not found.
            409 if job is already claimed or not in QUEUED status.
        """
        worker_id = request.data.get('worker_id')
        if not worker_id:
            return Response(
                {"error": "Missing 'worker_id' in request body."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Prevent worker identity spoofing: the authenticated worker
        # must match the worker_id in the request body.
        if hasattr(request.user, 'worker_profile'):
            if str(request.user.worker_profile.pk) != str(worker_id):
                return Response(
                    {"detail": "Cannot claim jobs on behalf of "
                     "another worker."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        try:
            worker = Worker.objects.get(pk=worker_id)
        except Worker.DoesNotExist:
            return Response(
                {"error": f"Worker with id '{worker_id}' not found."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            with transaction.atomic():
                job = Job.objects.select_for_update().get(pk=pk)

                if job.status != JobStatus.QUEUED or job.assigned_worker is not None:
                    return Response(
                        {"error": "Job is not available for claiming."},
                        status=status.HTTP_409_CONFLICT,
                    )

                job.status = JobStatus.RENDERING
                job.assigned_worker = worker
                job.started_at = timezone.now()
                job.save()

        except Job.DoesNotExist:
            return Response(
                {"error": "Job not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        logger.info(
            f"Job '{job.name}' (ID: {job.pk}) claimed by worker "
            f"'{worker.hostname}' (ID: {worker.pk})."
        )
        serializer = self.get_serializer(job)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], parser_classes=[MultiPartParser])
    def upload_output(self, request, pk=None):
        """
        Action for a worker to upload the final rendered output file for a job.

        Expects a multipart/form-data request with a file field named `output_file`.
        Validates the file is a valid image within the configured size limit.
        Saving the file will trigger a signal to generate the thumbnail.
        """
        job = self.get_object()
        self._check_worker_owns_job(request, job)
        file_obj = request.data.get('output_file')

        if not file_obj:
            return Response(
                {"error": "Missing 'output_file' in request."},
                status=status.HTTP_400_BAD_REQUEST
            )

        error = self._validate_upload(file_obj)
        if error:
            return Response(
                {"error": error},
                status=status.HTTP_400_BAD_REQUEST
            )

        safe_name = self._sanitize_filename(file_obj.name)
        job.output_file.save(safe_name, file_obj, save=True)

        logger.info(
            f"Received output file for job ID {job.id}. "
            f"Saved to {job.output_file.name}"
        )
        serializer = self.get_serializer(job)
        return Response(serializer.data, status=status.HTTP_200_OK)

    # -- Upload validation helpers --

    # 100 MB default, overridable via settings
    MAX_UPLOAD_SIZE = getattr(
        django_settings, 'FILE_UPLOAD_MAX_MEMORY_SIZE', 104857600
    )

    @classmethod
    def _validate_upload(cls, file_obj):
        """
        Validate that the uploaded file is within size limits and is a
        genuine image that Pillow can open.

        Returns an error message string, or None if valid.
        """
        if file_obj.size > cls.MAX_UPLOAD_SIZE:
            limit_mb = cls.MAX_UPLOAD_SIZE / (1024 * 1024)
            size_mb = file_obj.size / (1024 * 1024)
            return (
                f"File size {size_mb:.1f}MB exceeds the "
                f"{limit_mb:.0f}MB limit."
            )

        # Verify the file is a valid image using Pillow
        try:
            file_obj.seek(0)
            with Image.open(file_obj) as img:
                img.verify()
            file_obj.seek(0)
        except Exception:
            return "Uploaded file is not a valid image."

        return None

    @staticmethod
    def _sanitize_filename(filename):
        """
        Strip path components and replace unsafe characters so the
        filename is safe for storage.
        """
        # Take only the basename to strip directory traversal
        name = os.path.basename(filename)
        # Replace anything that is not alphanumeric, dot, hyphen,
        # or underscore with an underscore
        name = re.sub(r'[^\w.\-]', '_', name)
        return name or 'output.png'
