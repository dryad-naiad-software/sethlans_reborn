# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
System information endpoint.

Provides runtime system capabilities (e.g., ffmpeg availability)
to the frontend so it can conditionally enable features.
"""

from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from ..apps import WorkersConfig
from ..permissions import IsAdmin


@extend_schema(tags=['Management UI'])
@api_view(['GET'])
@permission_classes([IsAdmin])
def system_info_view(request):
    """Return system capabilities for the frontend."""
    return Response({
        'ffmpeg_available': WorkersConfig.ffmpeg_detected,
    })
