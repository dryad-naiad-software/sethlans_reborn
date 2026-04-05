# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
ViewSet for the global queue pause/resume setting.
"""

import logging

from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from ..models import QueueSetting
from ..permissions import IsAdmin

logger = logging.getLogger(__name__)


@extend_schema_view(
    list=extend_schema(tags=['Management UI']),
    pause=extend_schema(tags=['Management UI']),
    resume=extend_schema(tags=['Management UI']),
)
class QueueSettingViewSet(viewsets.ViewSet):
    """
    API endpoint for global queue control.

    Allows administrators to pause and resume the global job queue.
    When paused, workers will not receive any jobs during polling.
    """
    permission_classes = [IsAdmin]

    def list(self, request):
        """Return the current queue pause state."""
        setting = QueueSetting.get_instance()
        return Response({"queue_paused": setting.queue_paused})

    @action(detail=False, methods=['post'])
    def pause(self, request):
        """Pause the global job queue."""
        QueueSetting.objects.update_or_create(
            pk=1, defaults={'queue_paused': True},
        )
        logger.info("Global job queue paused by admin.")
        return Response({"queue_paused": True})

    @action(detail=False, methods=['post'])
    def resume(self, request):
        """Resume the global job queue."""
        QueueSetting.objects.update_or_create(
            pk=1, defaults={'queue_paused': False},
        )
        logger.info("Global job queue resumed by admin.")
        return Response({"queue_paused": False})
