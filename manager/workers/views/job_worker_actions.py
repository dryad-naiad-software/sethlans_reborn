# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Worker-facing job actions (claim, upload_output).

Extracted from jobs.py to keep file sizes under the 300-line limit.
"""

import logging

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response

from ..models import Job, JobStatus, Worker
from .upload_helpers import sanitize_filename, validate_upload

logger = logging.getLogger(__name__)


class JobWorkerActionsMixin:
    """Mixin providing worker-facing actions for JobViewSet."""

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
                job = Job.objects.select_for_update().select_related(
                    'blender_version',
                    'asset__project__blender_version',
                ).get(pk=pk)

                if job.status != JobStatus.QUEUED or job.assigned_worker is not None:
                    return Response(
                        {"error": "Job is not available for claiming."},
                        status=status.HTTP_409_CONFLICT,
                    )

                # Version compatibility check (defense-in-depth)
                effective = job.effective_blender_version
                if effective and not worker.has_blender_version(
                    effective.resolved_version,
                ):
                    return Response(
                        {"error": (
                            "Worker does not have required Blender "
                            f"version {effective.resolved_version} "
                            "installed."
                        )},
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

        if job.status == JobStatus.CANCELED:
            return Response(
                {"error": "Cannot upload output for a canceled job."},
                status=status.HTTP_409_CONFLICT,
            )

        self._check_worker_owns_job(request, job)
        file_obj = request.data.get('output_file')

        if not file_obj:
            return Response(
                {"error": "Missing 'output_file' in request."},
                status=status.HTTP_400_BAD_REQUEST
            )

        error = validate_upload(file_obj)
        if error:
            return Response(
                {"error": error},
                status=status.HTTP_400_BAD_REQUEST
            )

        safe_name = sanitize_filename(file_obj.name)
        job.output_file.save(safe_name, file_obj, save=True)

        logger.info(
            f"Received output file for job ID {job.id}. "
            f"Saved to {job.output_file.name}"
        )
        serializer = self.get_serializer(job)
        return Response(serializer.data, status=status.HTTP_200_OK)
