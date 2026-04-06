# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Video-related actions for the AnimationViewSet.

Provides retry_video and download_video as a mixin to keep
the main animations.py view file under the 300-line limit.
"""

import logging
import os
import threading

from django.conf import settings
from django.db import transaction
from django.http import FileResponse
from django.utils.text import slugify
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.response import Response

from ..constants import CONTAINER_CONTENT_TYPES
from ..models import Animation

logger = logging.getLogger(__name__)


class AnimationVideoActionsMixin:
    """Mixin providing retry-video and download-video actions."""

    @extend_schema(tags=['Management UI'])
    @action(detail=True, methods=['post'], url_path='retry-video')
    def retry_video(self, request, pk=None):
        """Retry failed video assembly for this animation."""
        from ..video_assembler import assemble_animation_video

        with transaction.atomic():
            try:
                animation = Animation.objects.select_for_update().get(pk=pk)
            except Animation.DoesNotExist:
                return Response({"error": "Animation not found."}, status=404)

            if animation.video_status != 'ERROR':
                return Response(
                    {"error": "Video assembly can only be retried when status is ERROR."},
                    status=400,
                )

            # Delete partial video file if it exists
            if animation.video_file:
                animation.video_file.delete(save=False)

            # Set directly to ASSEMBLING (skip PENDING to avoid CAS race)
            Animation.objects.filter(pk=animation.pk).update(
                video_status='ASSEMBLING',
                video_error='',
            )

            transaction.on_commit(
                lambda aid=animation.pk: threading.Thread(
                    target=assemble_animation_video,
                    args=(aid,),
                    daemon=True,
                ).start()
            )

        return Response({"status": "retry_started"})

    @extend_schema(tags=['Management UI'])
    @action(detail=True, methods=['get'], url_path='download-video')
    def download_video(self, request, pk=None):
        """Download the assembled video file for this animation."""
        animation = self.get_object()

        if animation.video_status != 'DONE' or not animation.video_file:
            return Response(
                {"error": "No video available for download."},
                status=404,
            )

        # MEDIA_ROOT containment check
        real_path = os.path.realpath(animation.video_file.path)
        media_root = os.path.realpath(settings.MEDIA_ROOT)
        if not real_path.startswith(media_root):
            return Response(
                {"error": "No video available for download."},
                status=404,
            )

        container = animation.video_settings.get('container', 'mp4')
        content_type = CONTAINER_CONTENT_TYPES.get(container, 'application/octet-stream')
        safe_name = slugify(animation.name) or 'animation'
        filename = f"{safe_name}.{container}"

        response = FileResponse(
            open(real_path, 'rb'),
            content_type=content_type,
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
