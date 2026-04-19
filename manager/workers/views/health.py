# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Health endpoint (``GET /api/health/``).

Returns the manager boot_id (fresh UUID per process start) plus the
manager version string.  Anonymous and always allowlisted by the setup
gate so the Angular wizard can poll it during the restart flow
(FR-14c / FR-15).
"""

from drf_spectacular.utils import extend_schema
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from sethlans_manager import runtime_state


@extend_schema(tags=["System"])
@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def health_view(request):
    """Return ``{"boot_id": str, "version": str}``.

    ``boot_id`` changes on every manager start — the Angular done-step
    polls this to detect a completed restart (FR-15).
    """
    # Imported lazily so tests can stub the module-level version.
    from sethlans_manager import __version__ as manager_version

    boot_id = runtime_state.manager_boot_id or ""
    return Response({
        "boot_id": boot_id,
        "version": manager_version,
    })
