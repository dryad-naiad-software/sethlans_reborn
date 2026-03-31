# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
# Created by Mario Estrella on 07/22/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
# workers/views/jobs.py

import logging

from django.db import transaction
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response

from ..constants import RenderDevice
from ..models import Job, JobStatus, Worker
from ..serializers import JobSerializer

logger = logging.getLogger(__name__)


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
        Saving the file will trigger a signal to generate the thumbnail.

        Args:
            request: The request object containing the uploaded file.
            pk: The primary key of the job to attach the file to.

        Returns:
            A Response containing the updated job data.
        """
        job = self.get_object()
        file_obj = request.data.get('output_file')

        if not file_obj:
            return Response(
                {"error": "Missing 'output_file' in request."},
                status=status.HTTP_400_BAD_REQUEST
            )

        job.output_file.save(file_obj.name, file_obj, save=True)

        logger.info(f"Received output file for job ID {job.id}. Saved to {job.output_file.name}")
        serializer = self.get_serializer(job)
        return Response(serializer.data, status=status.HTTP_200_OK)
