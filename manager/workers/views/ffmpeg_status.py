# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``GET /api/ffmpeg-status/`` — role-aware FFmpeg / video-assembly status.

Authentication: ``TokenAuthentication`` (workers) and
``SessionAuthentication`` (Angular admin via cookie).
Permission: ``IsAuthenticated``.

Regular users get ``{ "video_assembly_ready": <bool> }``.
Staff users additionally get an ``ffmpeg`` block with
``source / version / path / status / error``.

The response carries ``Cache-Control: no-store`` because status changes
at most once per process lifetime (boot transition); no intermediary
should cache the path or error.

This endpoint is a one-shot read.  No polling cadence — pages fetch
once on load.
"""

from drf_spectacular.utils import (
    OpenApiResponse, PolymorphicProxySerializer, extend_schema,
)
from rest_framework.authentication import (
    SessionAuthentication, TokenAuthentication,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ..serializers.ffmpeg_status import (
    FFmpegStatusAdminSerializer, FFmpegStatusSerializer,
)
from ..services import parts_check


class FFmpegStatusView(APIView):
    """Role-aware FFmpeg status endpoint."""

    authentication_classes = [TokenAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Management UI"],
        responses={
            200: PolymorphicProxySerializer(
                component_name="FFmpegStatus",
                serializers=[
                    FFmpegStatusSerializer,
                    FFmpegStatusAdminSerializer,
                ],
                resource_type_field_name=None,
            ),
            401: OpenApiResponse(
                description="Authentication required.",
            ),
        },
        description=(
            "Role-aware FFmpeg / video-assembly status. Regular users "
            "get a single boolean (`video_assembly_ready`); staff "
            "users additionally get an `ffmpeg` block with source, "
            "version, path, status, and error. Status changes at most "
            "once per process lifetime; the response carries "
            "`Cache-Control: no-store`."
        ),
    )
    def get(self, request):
        snapshot = parts_check.get_status("ffmpeg")
        if request.user.is_staff:
            serializer = FFmpegStatusAdminSerializer(snapshot)
        else:
            serializer = FFmpegStatusSerializer(snapshot)
        response = Response(serializer.data)
        response["Cache-Control"] = "no-store"
        return response
