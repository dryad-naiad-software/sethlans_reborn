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

Issue #225: a report from the assigned worker on a job that is already
CANCELED, DONE or ERROR (an admin cancel, or the reaper's max-requeue
ERROR) gets the same 409 before the serializer runs.  The serializer
would otherwise reject e.g. CANCELED -> CANCELED with a 400, which the
worker logs at ERROR; it logs the 409 at INFO.
"""

from rest_framework import status
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError

from ..models import JobStatus
from ..renderers import get_strategy
from ..services.worker_job_writes import (
    WORKER_PATCH_FIELDS, JobWriteConflict, PilotDoneRefused, apply_admin_patch,
    apply_worker_patch,
)


# Statuses a worker report can no longer change (issue #225).
_ALREADY_TERMINAL = frozenset({
    JobStatus.CANCELED, JobStatus.DONE, JobStatus.ERROR,
})


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

    def _reject_report_on_terminal_job(self, request, job):
        """Raise :class:`JobConflict` if the assignee reports on a finished job.

        Runs before validation, on the copy read for this request; the
        locked re-check in ``perform_update`` still covers a change
        committed after that read.  Other workers fall through to the
        ownership check (403).
        """
        worker = getattr(request.user, 'worker_profile', None)
        if worker is None or job.assigned_worker_id != worker.pk:
            return
        if job.status in _ALREADY_TERMINAL:
            raise JobConflict({'error': (
                f"Job is already {job.status}; the update was not applied."
            )})

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
                job = self._admin_patch(serializer.instance, changes)
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
        except PilotDoneRefused as exc:
            raise ValidationError({'detail': str(exc)})
        serializer.instance = job

    def _admin_patch(self, instance, changes):
        """Lock the project first and run the update hook (FR-PM-10, trigger a').

        The renderer was validated already (``serializer.validate``).
        """
        strategy = get_strategy(instance.renderer)
        project = instance.asset.project
        return apply_admin_patch(
            instance.pk, changes,
            before=lambda: strategy.lock_for_write(project),
            after=lambda job: strategy.after_job_update(job, set(changes)),
        )
