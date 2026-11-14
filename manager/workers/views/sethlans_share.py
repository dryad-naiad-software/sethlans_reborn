# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``GET`` / ``PUT /api/sethlans-share/``: the manager's Sethlans share
setting (Unreal foundation FR-MS-1, FR-MS-2, D3b).

The value is the manager's own: where Unreal projects live and what each
worker's reported share is compared against for routing.  It is never
sent to workers; each worker is configured with its share locally.

* ``PUT`` is admin-only.  The value is stored canonical and every change
  is logged at WARNING (audit).  Only ``sethlans_share`` is saved
  (``update_fields``), so a concurrent enrollment-key rotation is never
  overwritten and ``enrollment_key_updated_at`` does not change.
* ``GET`` is open to any authenticated caller except a worker token.

Changing or clearing the root never touches existing assets: workers
still configured with the old root stop matching and are refused Unreal
claims until their own ``SETHLANS_UNREAL_SHARE`` is changed.
"""

import logging

from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from shared.unreal.unc import is_single_label_host, validate_unc_dir

from ..models import ManagerSettings
from ..permissions import IsAdminNotWorker, IsAuthenticatedNonWorker
from ..serializers.sethlans_share import SethlansShareResponseSerializer, SethlansShareSerializer
from ..services.sethlans_share import configured_share

logger = logging.getLogger(__name__)

SINGLE_LABEL_WARNING = (
    "Use a DNS-resolvable fully qualified host name or an address; "
    "single-label names may be resolved by broadcast."
)


def _loggable(value):
    """``value`` with non-printable characters escaped (unvalidated text in the audit line)."""
    return ''.join(ch if ch.isprintable() else repr(ch)[1:-1] for ch in value)


class SethlansShareView(APIView):
    """Read or set the farm's Sethlans share root."""

    def get_permissions(self):
        if self.request.method == 'PUT':
            return [IsAdminNotWorker()]
        return [IsAuthenticatedNonWorker()]

    @extend_schema(tags=['Management UI'], responses=SethlansShareResponseSerializer)
    def get(self, request):
        """Return ``{"sethlans_share": "<canonical root or ''>"}``."""
        return Response({'sethlans_share': configured_share()})

    @extend_schema(
        tags=['Management UI'],
        request=SethlansShareSerializer,
        responses={200: SethlansShareResponseSerializer},
    )
    def put(self, request):
        """Store the share root in canonical form; ``""`` clears it."""
        serializer = SethlansShareSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new = serializer.validated_data['sethlans_share']
        with transaction.atomic():
            # Single table: the old value in the audit line is the one replaced.
            settings_row = ManagerSettings.objects.select_for_update().filter(pk=1).first()
            if settings_row is None:
                logger.error("Cannot set the Sethlans share: the ManagerSettings row is missing.")
                return Response(
                    {"error": "Manager settings are not initialized; run the setup first."},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            old = settings_row.sethlans_share
            settings_row.sethlans_share = new
            settings_row.save(update_fields=['sethlans_share'])
        logger.warning(
            "Sethlans share changed by %s from %s: '%s' -> '%s'",
            _loggable(request.user.get_username()), request.META.get('REMOTE_ADDR', ''),
            _loggable(old), new,
        )
        body = {'sethlans_share': new}
        if new and is_single_label_host(validate_unc_dir(new)):
            body['warnings'] = [SINGLE_LABEL_WARNING]
        return Response(body, status=status.HTTP_200_OK)
