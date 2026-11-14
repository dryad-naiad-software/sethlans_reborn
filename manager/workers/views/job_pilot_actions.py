# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``POST /api/jobs/{id}/pilot-result/`` and the manager-owned pilot rules
(Unreal foundation FR-PM-4, FR-PM-9, FR-RT-14).

The check order is normative (FR-PM-4); the first failing check answers:

1. shape: unknown keys at any level, then ``PilotResultRequestSerializer``
   (400, before any database access);
2. the job id: missing, or not all digits (404);
3. ownership of the RENDERING job, under the job lock (409);
4. a renderer without pilot measurement (409);
5. not a pilot job (409);
6. the job-dependent rules (400);
7. a CVar hash other than the record's (409; the pilot becomes ERROR).

Steps 3-7 live in ``services/pilot_result.py``.  A pilot becomes DONE only
here: a PATCH or PUT to DONE on a pilot is refused for every caller.
"""

from django.db import transaction
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from ..constants import UnrealKind
from ..models import Job, JobStatus
from ..renderers import UnsupportedRendererError, get_strategy
from ..serializers.unreal_measurement import (
    PilotGpuSerializer, PilotResultRequestSerializer, PilotResultResponseSerializer,
    PilotShotSerializer,
)
from ..services.pilot_result import record_pilot_result
from ..services.row_locks import lock_own_rows
from ..services.worker_job_writes import pilot_done_message, pilot_requeue_message
from ..strict_json import unknown_keys

_ERROR = inline_serializer('PilotResultError', {'error': serializers.CharField()})


def refuse_pilot_done(job, data):
    """Raise a 400 when a write would set a pilot to DONE (FR-RT-14, FR-PM-9)."""
    if (job.unreal_kind == UnrealKind.PILOT and hasattr(data, 'get')
            and data.get('status') == JobStatus.DONE):
        raise ValidationError({'detail': pilot_done_message(job)})


def pilot_requeue_refusal(job):
    """The 409 text for ``requeue/`` on a pilot, else ``None`` (FR-PM-9)."""
    if job.unreal_kind != UnrealKind.PILOT:
        return None
    return pilot_requeue_message(job)


def _unknown_body_keys(data):
    request_fields = PilotResultRequestSerializer().fields
    return unknown_keys(
        data, set(request_fields),
        nested={'gpu': set(PilotGpuSerializer().fields)},
        lists={'per_shot': set(PilotShotSerializer().fields)},
    )


def measures_pilots(renderer):
    """Whether a renderer's jobs can carry measurement records."""
    try:
        return get_strategy(renderer).supports('pilot_measurement')
    except UnsupportedRendererError:
        return False


class JobPilotActionsMixin:
    """Adds the worker's ``pilot_result`` action and the locked delete to ``JobViewSet``."""

    def perform_destroy(self, instance):
        """Lock the job row before deleting it (FR-PM-10 lock order).

        ``pilot-result/`` locks the Job, then writes its UnrealMeasurement;
        the delete's ``SET_NULL`` of ``pilot_job`` writes the measurement
        too, so taking the Job lock first keeps one order (Job ->
        UnrealMeasurement) and no deadlock.  Other renderers keep the
        plain delete.
        """
        if not measures_pilots(instance.renderer):
            return super().perform_destroy(instance)
        with transaction.atomic():
            locked = lock_own_rows(Job.objects).filter(pk=instance.pk).first()
            if locked is not None:
                instance.delete()

    @extend_schema(
        tags=['Worker Agent'],
        request=PilotResultRequestSerializer,
        responses={
            200: PilotResultResponseSerializer,
            400: OpenApiResponse(
                response=inline_serializer(
                    'PilotResultValidationError', {'detail': serializers.CharField(required=False)},
                ),
                description=(
                    '{"detail": "Unknown keys: ..."}, or a per-field error object '
                    '{"<field>": ["<message>", ...]} (types, bounds, engine build, '
                    'GPU cross-check, plausibility).'
                ),
            ),
            404: _ERROR, 409: _ERROR,
        },
    )
    @action(detail=True, methods=['post'], url_path='pilot-result')
    def pilot_result(self, request, pk=None):
        """Store a successful pilot's measurement and finish the pilot (FR-PM-4).

        Returns:
            200 ``{"status": "DONE", "peak_mb": n, "gate_need_mb": need}``;
            400 for the body or a job-dependent rule; 404 for an unknown
            job; 409 when the job is not rendering on this worker, is not
            a pilot, or rendered other CVars.  A repeat gets 409.
        """
        unknown = _unknown_body_keys(request.data)
        if unknown:
            return Response({'detail': f"Unknown keys: {', '.join(unknown)}."},
                            status=status.HTTP_400_BAD_REQUEST)
        body = PilotResultRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        not_found = Response({'error': 'Job not found.'}, status=status.HTTP_404_NOT_FOUND)
        if not (str(pk).isascii() and str(pk).isdigit() and len(str(pk)) <= 18):
            return not_found
        try:
            code, payload = record_pilot_result(int(pk), request.user.worker_profile, body.validated_data)
        except Job.DoesNotExist:
            return not_found
        return Response(payload, status=code)
