# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The one-time enrollment key notice of the dashboard (setup-rebuild FR-WZ-9).

``GET /api/auth/enrollment_key_notice/`` -> ``{"show": bool,
"enrollment_key": <hyphenated display key> | null}``: the key is sent only
while the notice is shown. ``POST`` dismisses it for good
(``ManagerSettings.enrollment_key_notice_dismissed``) and answers
``{"show": false, "enrollment_key": null}``. Staff only and never a
worker account (``IsAdminNotWorker``, #220), like
``regenerate_enrollment_key_view``; a key rotation does not show the
notice again (that view returns the new key itself). Both answers carry
``Cache-Control: no-store``. The key is never logged.
"""
import logging

from django.db import transaction
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from .. import enrollment_key
from ..models import ManagerSettings
from ..permissions import IsAdminNotWorker

logger = logging.getLogger(__name__)

_NOTICE = inline_serializer(
    name="EnrollmentKeyNotice",
    fields={
        "show": serializers.BooleanField(),
        "enrollment_key": serializers.CharField(allow_null=True),
    },
)
_COMMON = {
    403: OpenApiResponse(description="Not logged in, or not an administrator"),
}


def _hidden() -> dict:
    return {"show": False, "enrollment_key": None}


def _answer(body: dict) -> Response:
    response = Response(body)
    response["Cache-Control"] = "no-store"
    return response


@extend_schema(
    methods=["GET"],
    tags=["Worker Enrollment"],
    responses={200: _NOTICE, **_COMMON},
    description=(
        "Whether the dashboard shows the one-time enrollment key notice; "
        "the key in display form while it is shown, else null. Admin-only; "
        "the response carries `Cache-Control: no-store`."
    ),
)
@extend_schema(
    methods=["POST"],
    tags=["Worker Enrollment"],
    request=None,
    responses={200: _NOTICE, **_COMMON},
    description=(
        "Dismiss the enrollment key notice for good. Admin-only; "
        "the response carries `Cache-Control: no-store`."
    ),
)
@api_view(["GET", "POST"])
@permission_classes([IsAdminNotWorker])
def enrollment_key_notice_view(request):
    """GET the notice; POST dismisses it (module doc)."""
    if request.method == "POST":
        with transaction.atomic():
            row = ManagerSettings.objects.select_for_update().get(pk=1)
            if not row.enrollment_key_notice_dismissed:
                row.enrollment_key_notice_dismissed = True
                row.save(update_fields=["enrollment_key_notice_dismissed"])
                logger.info("Enrollment key notice dismissed by %s.", request.user.username)
        return _answer(_hidden())
    row = ManagerSettings.objects.only("enrollment_key", "enrollment_key_notice_dismissed").get(pk=1)
    if row.enrollment_key_notice_dismissed:
        return _answer(_hidden())
    return _answer({"show": True, "enrollment_key": enrollment_key.format_display(row.enrollment_key)})
