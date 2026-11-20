# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Worker result endpoints of Unreal sequence rendering (spec FR-SD-8, D12).

``POST /api/jobs/{id}/shots-result/`` (worker tokens only; body class
``RESULT_BODY_LIMIT``, D21).  The check order follows ``pilot-result/``:

1. shape: unknown keys at any level, then the shared strict check
   (400, before any database access);
2. the job id: missing, or not all digits (404);
3. under ``lock_project``, then the job lock: the RENDERING job is the
   caller's (409) and a ``shots`` job (409), and its discovery still
   waits (409).

A ``shots`` job becomes DONE only here: a PATCH or PUT to DONE is refused
for every caller (``services/job_kind_rules.py``).
"""

from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.response import Response

from shared.unreal.shots_result import FACT_KEYS, TOP_KEYS

from ..models import Job
from ..serializers.sequence_results import ShotsResultRequestSerializer, ShotsResultResponseSerializer
from ..services.sequence_discovery import record_shots_result
from ..strict_json import unknown_keys

_ERROR = inline_serializer('ShotsResultError', {'error': serializers.CharField()})


def _job_pk(pk):
    """The job id as an int, or ``None`` when it is not a plain number."""
    text = str(pk)
    return int(text) if text.isascii() and text.isdigit() and len(text) <= 18 else None


class JobSequenceActionsMixin:
    """Adds the worker's ``shots_result`` action to ``JobViewSet``."""

    @extend_schema(
        tags=['Worker Agent'],
        request=ShotsResultRequestSerializer,
        responses={
            200: ShotsResultResponseSerializer,
            400: OpenApiResponse(
                response=inline_serializer(
                    'ShotsResultValidationError', {'detail': serializers.CharField(required=False)},
                ),
                description='{"detail": "Unknown keys: ..."}, or {"<field>": ["<message>"]}.',
            ),
            404: _ERROR, 409: _ERROR,
        },
    )
    @action(detail=True, methods=['post'], url_path='shots-result')
    def shots_result(self, request, pk=None):
        """Store a ``shots`` job's discovery result and finish the job (FR-SD-8).

        Returns:
            200 ``{"status": "DONE", "discovery": id, "shots": n}``; 400 for
            the body; 404 for an unknown job; 409 when the job is not
            rendering on this worker, is not a shots job, or its discovery
            no longer waits.  A repeat gets 409.
        """
        unknown = unknown_keys(request.data, set(TOP_KEYS), nested={'preset_facts': set(FACT_KEYS)})
        if unknown:
            return Response({'detail': f"Unknown keys: {', '.join(unknown)}."},
                            status=status.HTTP_400_BAD_REQUEST)
        body = ShotsResultRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        not_found = Response({'error': 'Job not found.'}, status=status.HTTP_404_NOT_FOUND)
        job_pk = _job_pk(pk)
        if job_pk is None:
            return not_found
        try:
            code, payload = record_shots_result(job_pk, request.user.worker_profile, body.validated_data)
        except Job.DoesNotExist:
            return not_found
        return Response(payload, status=code)
