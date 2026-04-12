# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Worker yield-event action mixin.

Provides the ``POST /api/heartbeat/{pk}/yield-event/`` endpoint
that records yield events from worker agents.
"""

import logging

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from ..models import Worker
from ..serializers import WorkerYieldEventSerializer

logger = logging.getLogger(__name__)


class WorkerYieldActionsMixin:
    """Mixin providing the yield-event action for WorkerHeartbeatViewSet."""

    @extend_schema(tags=['Worker Agent'])
    @action(
        detail=True,
        methods=['post'],
        url_path='yield-event',
    )
    def yield_event(self, request, pk=None):
        """Record a yield event for this worker."""
        try:
            worker = Worker.objects.get(pk=pk)
        except Worker.DoesNotExist:
            return Response(
                {"detail": "Worker not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Ownership check: the authenticated worker must match the
        # target worker pk to prevent workers posting events for
        # other workers.
        caller = getattr(request.user, 'worker_profile', None)
        if caller is None or caller.pk != worker.pk:
            return Response(
                {"detail": "Not authorized for this worker."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = WorkerYieldEventSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(worker=worker)

        logger.info(
            "Yield event recorded for worker %s: reason=%s, "
            "outcome=%s",
            worker.hostname,
            serializer.validated_data.get('reason'),
            serializer.validated_data.get('grace_outcome'),
        )

        return Response(
            serializer.data,
            status=status.HTTP_201_CREATED,
        )
