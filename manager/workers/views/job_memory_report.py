# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``POST /api/jobs/{id}/memory-report/`` (Unreal per-machine memory test
FR-MT-S-3, D9).  Worker tokens only; the default small body class.

Check order: the body's shape (400: unknown keys, then
``MemoryReportRequestSerializer``, before any database access), the job
id (404), then under the job lock (``services/memory_report.py``): the
job RENDERING on the caller (409), an Unreal render or audio job (409),
one report per job (an identical repeat 200 with the stored answer, a
different one 409), the card kind and the record (400).
"""

from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.response import Response

from ..models import Job
from ..serializers.memory_report import MemoryReportRequestSerializer, MemoryReportResponseSerializer
from ..services.memory_report import record_memory_report
from ..strict_json import unknown_keys
from .job_sequence_actions import _job_pk, _not_found

_ERROR = inline_serializer('MemoryReportError', {'error': serializers.CharField()})
_VALIDATION = OpenApiResponse(
    response=inline_serializer('MemoryReportValidationError', {'detail': serializers.CharField(required=False)}),
    description='{"detail": "Unknown keys: ..."}, or {"<field>": ["<message>"]} (types, bounds, card kind, record).',
)


class JobMemoryReportMixin:
    """Adds the worker's ``memory_report`` action to ``JobViewSet``."""

    @extend_schema(
        tags=['Worker Agent'], request=MemoryReportRequestSerializer,
        responses={200: MemoryReportResponseSerializer, 400: _VALIDATION, 404: _ERROR, 409: _ERROR},
    )
    @action(detail=True, methods=['post'], url_path='memory-report')
    def memory_report(self, request, pk=None):
        """Record a render or audio job's memory report: job peak, spill and driver (FR-MT-S-3).

        Returns:
            200 ``{"result": <pair state>}``; 400 for the body, a card kind
            other than the caller's card's, or a record that is not the
            caller's for this job; 404 for an unknown job; 409 when the job
            is not an Unreal render or audio job rendering on this worker,
            or a different report was already accepted.
        """
        unknown = unknown_keys(request.data, set(MemoryReportRequestSerializer().fields))
        if unknown:
            return Response({'detail': f"Unknown keys: {', '.join(unknown)}."}, status=status.HTTP_400_BAD_REQUEST)
        body = MemoryReportRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        job_pk = _job_pk(pk)
        if job_pk is None:
            return _not_found()
        try:
            code, payload = record_memory_report(job_pk, request.user.worker_profile, body.validated_data)
        except Job.DoesNotExist:
            return _not_found()
        return Response(payload, status=code)
