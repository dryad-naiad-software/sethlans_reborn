# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Worker-initiated release of a job, and the per-renderer worker PATCH
allowlist (Unreal foundation FR-RT-11, FR-RT-14, D4).

``POST /api/jobs/{id}/release/`` returns a RENDERING job to the queue when
its worker shuts down cleanly, cannot dispatch it, loses the Sethlans
share before the render starts, meets a local conflict, or stops it
because its graphics card was lost or changed (``worker_hardware_changed``,
the same reason and counting as the Blender hand-back through
yield-requeue).  Every reason goes through ``requeue_or_fail``, shared
with the stuck-job reaper, so the 4th requeue ends in ERROR with the
reaper's message.  Only renderers whose strategy supports release
(Unreal) accept it.
"""

import logging
from collections.abc import Mapping

from django.db import transaction
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from ..models import Job, JobStatus
from ..parent_status import take_write_lock
from ..renderers import UnsupportedRendererError, get_strategy
from ..services.row_locks import lock_own_rows
from .job_pilot_actions import refuse_pilot_done
from .stuck_jobs import requeue_or_fail

logger = logging.getLogger(__name__)

RELEASE_REASONS = (
    'shutdown', 'dispatch_failed', 'share_unavailable', 'local_conflict',
    'worker_hardware_changed',
)
NOT_RENDERING_HERE = "Job is not rendering on this worker."
RELEASE_UNSUPPORTED = "Release is not supported for this renderer."


class JobReleaseRequestSerializer(serializers.Serializer):
    """Body of ``POST /api/jobs/{id}/release/``."""

    reason = serializers.ChoiceField(choices=RELEASE_REASONS)


_RELEASE_RESPONSE = inline_serializer('JobReleaseResponse', {
    'status': serializers.ChoiceField(choices=[JobStatus.QUEUED, JobStatus.ERROR]),
    'auto_requeue_count': serializers.IntegerField(),
})
_ERROR_RESPONSE = inline_serializer('JobReleaseError', {'error': serializers.CharField()})


def _strategy_or_none(renderer):
    try:
        return get_strategy(renderer)
    except UnsupportedRendererError:
        return None


def _release_locked(pk, worker, reason):
    """Release job ``pk`` for ``worker`` under the row lock.

    Returns:
        ``(job, new_status)`` on success, or ``(None, error_message)``.

    Raises:
        Job.DoesNotExist: no such job.
    """
    with transaction.atomic():
        # A write first: SQLite takes its write lock now (no BUSY_SNAPSHOT).
        take_write_lock(Job.objects.filter(pk=pk))
        job = lock_own_rows(Job.objects).get(pk=pk)
        # Ownership first: a non-owner never learns the job's renderer.
        if job.status != JobStatus.RENDERING or job.assigned_worker_id != worker.pk:
            return None, NOT_RENDERING_HERE
        strategy = _strategy_or_none(job.renderer)
        if strategy is None or not strategy.supports_release():
            return None, RELEASE_UNSUPPORTED
        message = f"Auto-requeued: worker released {strategy.label} job ({reason})"
        return job, requeue_or_fail(job, message)


class JobReleaseActionsMixin:
    """Adds the ``release`` action and the worker PATCH allowlist to JobViewSet."""

    def _enforce_worker_patch_allowlist(self, request, job):
        """Refuse a worker PATCH key outside the strategy's allowlist (FR-RT-14).

        Runs before validation, so a worker gets this message rather than a
        strategy field error.  Renderers whose ``worker_patch_allowed_fields()``
        is ``None`` (Blender) are untouched.  The caller (``partial_update``)
        runs the ownership check (409, ``_check_worker_owns_job``) first, so
        a non-owner never reaches this and learns nothing about the job's
        renderer.

        Raises:
            ValidationError: a key outside the allowlist (400,
                ``{"detail": "Workers may not modify <keys> on <label> jobs."}``).
        """
        if getattr(request.user, 'worker_profile', None) is None:
            return
        strategy = _strategy_or_none(job.renderer)
        allowed = strategy.worker_patch_allowed_fields() if strategy is not None else None
        if allowed is None:
            return
        if not isinstance(request.data, Mapping):
            raise ValidationError({'detail': "Expected an object."})
        refused = sorted(set(request.data.keys()) - allowed)
        if refused:
            raise ValidationError({
                'detail': f"Workers may not modify {', '.join(refused)} on {strategy.label} jobs.",
            })
        # After the ownership 409 and the allowlist 400, before validation (FR-RT-14).
        refuse_pilot_done(job, request.data)

    @extend_schema(
        tags=['Worker Agent'],
        request=JobReleaseRequestSerializer,
        responses={200: _RELEASE_RESPONSE, 404: _ERROR_RESPONSE, 409: _ERROR_RESPONSE},
    )
    @action(detail=True, methods=['post'])
    def release(self, request, pk=None):
        """Return the caller's RENDERING job to the queue (D4).

        Returns:
            200 ``{"status": "QUEUED" | "ERROR", "auto_requeue_count": n}``;
                ERROR means the auto-requeue limit was exceeded.
            400 for a ``reason`` other than shutdown, dispatch_failed,
                share_unavailable, local_conflict or worker_hardware_changed.
            404 if the job does not exist.
            409 if the job is not RENDERING on this worker (checked first),
                or its renderer does not support release.  A repeated
                release after success gets 409, which the worker treats as
                done.
        """
        body = JobReleaseRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        reason = body.validated_data['reason']
        worker = request.user.worker_profile
        not_found = Response({"error": "Job not found."}, status=status.HTTP_404_NOT_FOUND)
        if not (str(pk).isascii() and str(pk).isdigit()):
            return not_found
        try:
            job, outcome = _release_locked(int(pk), worker, reason)
        except Job.DoesNotExist:
            return not_found
        if job is None:
            return Response({"error": outcome}, status=status.HTTP_409_CONFLICT)
        logger.info(
            "Job '%s' (ID: %s) released by worker '%s' (%s): now %s, "
            "auto_requeue_count %d.",
            job.name, job.pk, worker.hostname, reason, outcome,
            job.auto_requeue_count,
        )
        return Response(
            {"status": outcome, "auto_requeue_count": job.auto_requeue_count},
            status=status.HTTP_200_OK,
        )
