# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

from drf_spectacular.utils import extend_schema
from rest_framework.decorators import (
    api_view, authentication_classes, permission_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


@extend_schema(tags=['System'])
@api_view(['GET'])
@authentication_classes([])
@permission_classes([AllowAny])
def health_view(request):
    return Response({"status": "ok"})
