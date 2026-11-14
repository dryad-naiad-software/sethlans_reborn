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
from ..renderers import UnsupportedRendererError, get_strategy
from ..services.row_locks import lock_own_rows
from ..services.worker_job_writes import JobWriteConflict
from .job_output_upload import store_and_attach_output
from .upload_helpers import validate_upload

logger = logging.getLogger(__name__)

# ``_claim_outcome`` result: the assignee re-claimed its own job.
_RECLAIM = object()


def _claim_outcome(job, worker):
    """Decide a claim of the locked *job* by *worker*.

    Returns:
        ``None`` to claim the job, ``_RECLAIM`` for a repeated claim by
        the assignee that changes nothing (lost acknowledgement: the
        retried POST of a claim that already committed, FR-RT-4; only
        renderers whose strategy allows it), or the 409 rejection (any
        other value; the caller sends ``str()`` of it).
    """
    try:
        strategy = get_strategy(job.renderer)
    except UnsupportedRendererError:
        return f"Unsupported renderer '{job.renderer}'."
    if (strategy.idempotent_reclaim()
            and job.status == JobStatus.RENDERING
            and job.assigned_worker_id == worker.pk):
        return _RECLAIM
    # ``assigned_worker_id``: no lazy Worker SELECT while the row is locked.
    if job.status != JobStatus.QUEUED or job.assigned_worker_id is not None:
        return "Job is not available for claiming."
    if job.is_paused:
        return "Job is paused and cannot be claimed."
    # Renderer compatibility (defense-in-depth; Blender: the worker must
    # have the job's Blender version installed).
    return strategy.claim_rejection(worker, job)


def _claiming_worker(request):
    """Return ``(worker, None)`` for the claim body, or ``(None, response)``.

    The authenticated worker must match ``worker_id`` (no spoofing).
    """
    worker_id = request.data.get('worker_id')
    if not worker_id:
        return None, Response(
            {"error": "Missing 'worker_id' in request body."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Prevent worker identity spoofing: the authenticated worker
    # must match the worker_id in the request body.
    if hasattr(request.user, 'worker_profile'):
        if str(request.user.worker_profile.pk) != str(worker_id):
            return None, Response(
                {"detail": "Cannot claim jobs on behalf of "
                 "another worker."},
                status=status.HTTP_403_FORBIDDEN,
            )

    try:
        return Worker.objects.get(pk=worker_id), None
    except Worker.DoesNotExist:
        return None, Response(
            {"error": f"Worker with id '{worker_id}' not found."},
            status=status.HTTP_400_BAD_REQUEST,
        )


class JobWorkerActionsMixin:
    """Mixin providing worker-facing actions for JobViewSet."""

    @action(detail=True, methods=['post'])
    def claim(self, request, pk=None):
        """
        Atomically claim a job for a worker.

        Uses SELECT FOR UPDATE to lock the job row, preventing race
        conditions where multiple workers claim the same job.

        Expects a JSON body with `worker_id` (the Worker primary key).

        The renderer strategy decides whether this worker may render the
        job (``claim_rejection``) and whether a repeated claim by the
        assignee is acknowledged (``idempotent_reclaim``; Blender: no).

        Returns:
            200 on success with updated job data.
            400 if worker_id is missing or invalid.
            404 if job not found.
            409 if job is already claimed or not in QUEUED status, or the
                renderer strategy rejects this worker.
        """
        worker, error_response = _claiming_worker(request)
        if error_response is not None:
            return error_response

        try:
            with transaction.atomic():
                # Lock only the job row: the joins are nullable, and
                # PostgreSQL rejects FOR UPDATE on an outer join (#216).
                job = lock_own_rows(
                    Job.objects,
                    'blender_version',
                    'asset__project__blender_version',
                    'asset__project',
                ).get(pk=pk)
                outcome = _claim_outcome(job, worker)
                # Fail closed: only ``None`` claims; anything other than
                # ``_RECLAIM`` (whatever its type) is a rejection.
                if outcome is None:
                    job.status = JobStatus.RENDERING
                    job.assigned_worker = worker
                    job.started_at = timezone.now()
                    job.save()
                elif outcome is not _RECLAIM:
                    return Response(
                        {"error": str(outcome)},
                        status=status.HTTP_409_CONFLICT,
                    )

        except Job.DoesNotExist:
            return Response(
                {"error": "Job not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if outcome is _RECLAIM:
            logger.info(
                "Job '%s' (ID: %s) re-claimed by its assignee '%s'.",
                job.name, job.pk, worker.hostname,
            )
        else:
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

        Expects a multipart/form-data request with a file field named ``output_file``
        and an optional ``thumbnail`` file field (PNG). Validates both files.
        Saving the output file triggers a post_save signal for thumbnail generation.

        Issue #217: the files are stored first, then the job row is locked
        and re-checked (still RENDERING, still assigned to this worker)
        and only ``output_file``/``thumbnail`` are saved.  A job canceled
        or requeued meanwhile gets 409, an unchanged row and no stored
        files.  ``post_save`` defers server-side thumbnail generation to
        ``transaction.on_commit`` (#118), and SQLite lock errors retry the
        locked write (``run_with_lock_retry``).
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

        try:
            job = store_and_attach_output(
                job, request.user.worker_profile, file_obj,
                thumb_file=request.data.get('thumbnail'),
            )
        except JobWriteConflict as exc:
            logger.warning(
                "Rejected output upload for job %s: %s", pk, exc,
            )
            return Response(
                {"error": str(exc)}, status=status.HTTP_409_CONFLICT,
            )

        logger.info(
            f"Received output file for job ID {job.id}. "
            f"Saved to {job.output_file.name}"
        )
        serializer = self.get_serializer(job)
        return Response(serializer.data, status=status.HTTP_200_OK)
