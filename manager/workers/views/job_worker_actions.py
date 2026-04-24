# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Worker-facing job actions (claim, upload_output).

Extracted from jobs.py to keep file sizes under the 300-line limit.
"""

import logging
import time

from django.db import OperationalError, transaction
from django.utils import timezone
from PIL import Image
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response

from ..models import Job, JobStatus, Worker
from .upload_helpers import sanitize_filename, validate_upload

logger = logging.getLogger(__name__)

# Maximum size for worker-provided thumbnails (1 MB)
MAX_THUMBNAIL_SIZE = 1 * 1024 * 1024

# Issue #118: brief retry on SQLite lock-upgrade failures that
# ``busy_timeout`` cannot wait on (``SQLITE_LOCKED``).  Each retry doubles
# the back-off starting at 50 ms; with 6 attempts the worst case is ~3.1 s
# of retry before surfacing the error, well inside the 30 s HTTP window and
# far below the 5 s e2e concurrency-test budget.
_SAVE_RETRY_ATTEMPTS = 6
_SAVE_RETRY_BASE_DELAY_S = 0.05


def _save_output_with_lock_retry(job, safe_name, file_obj):
    """Persist ``output_file`` with a short retry on SQLite lock errors.

    SQLite (especially in shared-cache mode used by Django's test runner)
    can raise ``OperationalError: database table is locked`` when a
    concurrent connection holds a write lock on the same table — this
    error is *not* covered by ``PRAGMA busy_timeout``, which only retries
    ``SQLITE_BUSY``.  Production SQLite under WAL almost never hits this,
    but when it does we'd rather pay a few milliseconds of back-off than
    bubble a 500 to the worker.  Non-sqlite databases (Postgres / MySQL)
    never see this error so the retry is effectively a no-op for them.
    """
    delay = _SAVE_RETRY_BASE_DELAY_S
    last_exc = None
    for attempt in range(_SAVE_RETRY_ATTEMPTS):
        try:
            job.output_file.save(safe_name, file_obj, save=True)
            return
        except OperationalError as exc:
            msg = str(exc).lower()
            if "locked" not in msg:
                raise
            last_exc = exc
            logger.warning(
                "upload_output for job %s hit a SQLite lock on attempt "
                "%d/%d: %s. Retrying in %.3fs.",
                job.pk, attempt + 1, _SAVE_RETRY_ATTEMPTS, exc, delay,
            )
            time.sleep(delay)
            delay *= 2
            # Rewind the upload so the next attempt re-reads from byte 0.
            try:
                file_obj.seek(0)
            except Exception:
                pass
    # Exhausted retries — re-raise the last OperationalError so DRF maps
    # it to a 500 rather than returning a partial 200.
    raise last_exc


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

                if job.is_paused:
                    return Response(
                        {"error": "Job is paused and cannot be claimed."},
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

        Expects a multipart/form-data request with a file field named ``output_file``
        and an optional ``thumbnail`` file field (PNG). Validates both files.
        Saving the output file triggers a post_save signal for thumbnail generation.
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

        # Process optional worker-provided thumbnail
        thumb_file = request.data.get('thumbnail')
        if thumb_file:
            self._save_worker_thumbnail(job, thumb_file)

        safe_name = sanitize_filename(file_obj.name)

        # IMPORTANT: thumbnail.save(save=False) sets the field in memory only.
        # output_file.save(save=True) persists the entire model instance,
        # including the thumbnail field set above. This ordering is required
        # so that both fields are written in a single save() call and the
        # post_save signal sees the thumbnail as already present.
        #
        # Issue #118: the ``handle_job_completion`` ``post_save`` handler
        # defers the thumbnail generate + inner ``thumbnail.save`` pair to
        # ``transaction.on_commit``, shrinking the critical section to a
        # single ``UPDATE workers_job``.  The save is wrapped by
        # ``_save_job_with_lock_retry`` to handle the narrow
        # ``SQLITE_LOCKED`` window that the ``busy_timeout`` PRAGMA does
        # not cover (shared-cache / lock-upgrade races on SQLite).
        _save_output_with_lock_retry(job, safe_name, file_obj)

        logger.info(
            f"Received output file for job ID {job.id}. "
            f"Saved to {job.output_file.name}"
        )
        serializer = self.get_serializer(job)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def _save_worker_thumbnail(self, job, thumb_file):
        """
        Validate and save a worker-provided thumbnail to the job.

        If validation fails, logs a warning and skips the thumbnail
        without failing the job.
        """
        try:
            # Validate file size
            if thumb_file.size > MAX_THUMBNAIL_SIZE:
                logger.warning(
                    "Worker-provided thumbnail for job %s failed "
                    "validation: file size %d bytes exceeds 1MB limit. "
                    "Saving output without thumbnail.",
                    job.id, thumb_file.size,
                )
                return

            # Validate it is a valid PNG image
            thumb_file.seek(0)
            try:
                with Image.open(thumb_file) as img:
                    img.verify()
            except Exception:
                logger.warning(
                    "Worker-provided thumbnail for job %s failed "
                    "validation: not a valid image file. "
                    "Saving output without thumbnail.",
                    job.id,
                )
                return
            thumb_file.seek(0)

            safe_name = sanitize_filename(thumb_file.name)
            job.thumbnail.save(safe_name, thumb_file, save=False)

        except Exception as e:
            logger.warning(
                "Worker-provided thumbnail for job %s failed "
                "validation: %s. Saving output without thumbnail.",
                job.id, e,
            )
