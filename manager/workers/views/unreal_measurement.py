# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``POST /api/projects/{id}/unreal-measurement/retry/`` (Unreal foundation
FR-PM-6).

A thin view: it checks the body strictly (unknown keys, real JSON types)
and calls ``services.unreal_pilot.retry_measurement``, which locks the
project first and raises ``RetryRefused`` for each refusal.  Admins only
(``IsAdminNotWorker``, #220); session requests are CSRF-checked.
"""

from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.response import Response

from ..permissions import IsAdminNotWorker
from ..serializers.unreal_measurement import (
    MeasurementRetryRequestSerializer, MeasurementRetryResponseSerializer,
)
from ..services.unreal_pilot import retry_measurement
from ..services.unreal_retry import RetryRefused
from ..strict_json import unknown_keys

_ERROR = inline_serializer('MeasurementRetryError', {'error': serializers.CharField()})
_BAD = OpenApiResponse(
    response=inline_serializer('MeasurementRetryBadRequest', {'detail': serializers.CharField(required=False)}),
    description=(
        '{"detail": "Unknown keys: ..."} or {"detail": "Not an Unreal project."}, or a per-field '
        'error object such as {"job_id": ["Not an Unreal job of this project."]}.'
    ),
)


class UnrealMeasurementActionsMixin:
    """Adds the admin measurement retry to ``ProjectViewSet``."""

    @extend_schema(
        tags=['Management UI'],
        request=MeasurementRetryRequestSerializer,
        responses={201: MeasurementRetryResponseSerializer, 400: _BAD, 403: _ERROR,
                   404: _ERROR, 409: _ERROR},
    )
    @action(detail=True, methods=['post'], url_path='unreal-measurement/retry',
            permission_classes=[IsAdminNotWorker])
    def unreal_measurement_retry(self, request, pk=None):
        """Create a new pilot for one measurement key of this project.

        Body (optional): ``{"job_id": <int>, "remeasure": <bool>}``.

        Returns:
            201 ``{"pilot_job_id": n, "measurement_id": n}``; 400 for a bad
            body, a Blender project or a ``job_id`` that is not a normal
            Unreal job of this project; 403 for a non-admin or a worker;
            404 for an unknown project; 409 when the key is pending,
            measured (without ``remeasure``), no held job needs one, or
            the project has no Unreal asset.
        """
        project = self.get_object()
        data = request.data if hasattr(request.data, 'keys') else None
        if data is None:
            return Response({'detail': "Expected an object."}, status=status.HTTP_400_BAD_REQUEST)
        unknown = unknown_keys(dict(data.items()), set(MeasurementRetryRequestSerializer().fields))
        if unknown:
            return Response({'detail': f"Unknown keys: {', '.join(unknown)}."},
                            status=status.HTTP_400_BAD_REQUEST)
        body = MeasurementRetryRequestSerializer(data=data)
        body.is_valid(raise_exception=True)
        try:
            record = retry_measurement(
                project, job_id=body.validated_data.get('job_id'),
                remeasure=body.validated_data.get('remeasure', False),
                username=request.user.get_username(),
            )
        except RetryRefused as exc:
            return Response(exc.body, status=exc.status)
        return Response(
            {'pilot_job_id': record.pilot_job_id, 'measurement_id': record.pk},
            status=status.HTTP_201_CREATED,
        )
