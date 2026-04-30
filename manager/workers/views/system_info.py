# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
System information endpoint.

Provides runtime system capabilities to the frontend so it can
conditionally enable features.

Note (wizard-ffmpeg-rewrite, spec FR §114-122):
    The legacy ``ffmpeg_available`` field has been removed from this
    endpoint.  FFmpeg / video-assembly status is now served by
    ``GET /api/ffmpeg-status/`` with a role-aware payload.  Frontend
    consumers migrate to the new ``FFmpegStatusService``.
"""

from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from ..permissions import IsAdmin


@extend_schema(tags=['Management UI'])
@api_view(['GET'])
@permission_classes([IsAdmin])
def system_info_view(request):
    """Return system capabilities for the frontend."""
    return Response({})
