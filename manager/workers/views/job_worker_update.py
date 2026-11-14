# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Worker ownership checks and ``perform_update`` for ``/api/jobs/{id}/``.

Issue #217: ``perform_update`` used to run ``serializer.save()``, which
saves every column of the unlocked job read at the start of the request,
so a cancel, requeue or worker report committed meanwhile was undone.
Both callers now write under the job row lock, only the fields they sent
(see ``services/worker_job_writes.py``):

* a worker PATCH may write only ``WORKER_PATCH_FIELDS``, and only while
  the job is RENDERING and assigned to it;
* an admin PATCH/PUT writes the validated fields it sent.
"""

from rest_framework import status
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError

from ..services.worker_job_writes import (
    WORKER_PATCH_FIELDS, JobWriteConflict, apply_admin_patch,
    apply_worker_patch,
)


class JobConflict(APIException):
    """HTTP 409 raised from ``perform_update`` (body ``{"error": ...}``)."""

    status_code = status.HTTP_409_CONFLICT
    default_detail = 'The job changed while the request was in progress.'
    default_code = 'conflict'


class JobWorkerUpdateMixin:
    """Mixin providing worker ownership checks and ``perform_update``."""

    def _check_worker_owns_job(self, request, job):
        """Verify that a worker-authenticated user owns this job."""
        if hasattr(request.user, 'worker_profile'):
            if request.user.worker_profile != job.assigned_worker:
                raise PermissionDenied(
                    "You do not have permission to modify this job."
                )

    def perform_update(self, serializer):
        """Save an update under the row lock, writing only the sent fields.

        Raises:
            PermissionDenied: the worker is not assigned to the job (403).
            ValidationError: the worker sent a field it may not write (400).
            JobConflict: the job was canceled, requeued or reassigned since
                it was read, or the status change is no longer valid (409).
                The row is left unchanged.
        """
        changes = dict(serializer.validated_data)
        job_pk = serializer.instance.pk
        worker = getattr(self.request.user, 'worker_profile', None)
        try:
            if worker is None:
                job = apply_admin_patch(job_pk, changes)
            else:
                self._check_worker_owns_job(self.request, serializer.instance)
                disallowed = sorted(set(changes) - WORKER_PATCH_FIELDS)
                if disallowed:
                    raise ValidationError({
                        field: ["Workers cannot change this field."]
                        for field in disallowed
                    })
                job = apply_worker_patch(job_pk, worker, changes)
        except JobWriteConflict as exc:
            raise JobConflict({'error': str(exc)})
        serializer.instance = job
