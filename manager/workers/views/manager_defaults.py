# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Manager defaults endpoint (FR-MA1).

``GET /api/manager-defaults/`` — requires ``TokenAuthentication``.
Returns the default Blender version and render engine for enrolled
workers to discover which Blender version to pre-download.
"""

import logging

from rest_framework.authentication import TokenAuthentication
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from workers.models import SupportedBlenderVersion

logger = logging.getLogger(__name__)


@api_view(["GET"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def manager_defaults_view(request):
    """GET /api/manager-defaults/ (FR-MA1).

    Returns the default Blender version and render engine.
    """
    default = SupportedBlenderVersion.objects.filter(
        is_default=True,
    ).first()

    if default is None:
        return Response({
            "default_blender_version": None,
            "default_render_engine": "CYCLES",
        })

    return Response({
        "default_blender_version": default.resolved_version,
        "default_render_engine": "CYCLES",
    })
