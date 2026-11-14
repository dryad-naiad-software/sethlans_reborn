# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Token management actions for workers (revoke, regenerate, force re-enroll).

Extracted from heartbeat.py to keep file sizes under the 300-line limit.

Every action here is admin-only (issue #220).  The host viewset's
``HeartbeatViewSetPermission`` lets any authenticated caller through for
non-``list`` actions so that worker tokens can heartbeat, and the
viewset must not override ``get_permissions`` (AC-21a, see
``tests/integration/manager/test_enroll_rotation.py``), so the
restriction is set per action via ``@action(permission_classes=...)``,
which DRF applies in place of the viewset-level list for that action.
Every action logs which user performed it.
"""

import logging

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.decorators import action
from rest_framework.response import Response

from ..models import Worker
from ..permissions import IsAdminNotWorker

logger = logging.getLogger(__name__)

# Staff only, and never a worker account or worker Token.  A worker
# (or a non-staff user) must never be able to revoke, rotate or read
# any worker's credentials, its own included.
ADMIN_ONLY = [IsAdminNotWorker]


class WorkerTokenActionsMixin:
    """Mixin providing admin-only token actions for WorkerHeartbeatViewSet."""

    @staticmethod
    def _require_worker_with_user(pk):
        """Return (worker, None) or (None, error Response)."""
        try:
            worker = Worker.objects.select_related('user').get(pk=pk)
        except Worker.DoesNotExist:
            return None, Response(
                {"detail": "Worker not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not worker.user:
            return None, Response(
                {"detail": "Worker has no linked user."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return worker, None

    @extend_schema(tags=['Management UI'])
    @action(
        detail=True, methods=['post'], url_path='revoke_token',
        permission_classes=ADMIN_ONLY,
    )
    def revoke_token(self, request, pk=None):
        """Delete the DRF Token associated with a worker's User."""
        worker, err = self._require_worker_with_user(pk)
        if err:
            return err
        Token.objects.filter(user=worker.user).delete()
        logger.info(
            "Token revoked for worker %s by user id=%s",
            worker.hostname, request.user.pk,
        )
        return Response({"detail": "Token revoked."})

    @extend_schema(tags=['Management UI'])
    @action(
        detail=True, methods=['post'], url_path='regenerate_token',
        permission_classes=ADMIN_ONLY,
    )
    def regenerate_token(self, request, pk=None):
        """Delete old token and create a new one. Returns new key."""
        worker, err = self._require_worker_with_user(pk)
        if err:
            return err
        Token.objects.filter(user=worker.user).delete()
        token = Token.objects.create(user=worker.user)
        logger.info(
            "Token regenerated for worker %s by user id=%s",
            worker.hostname, request.user.pk,
        )
        return Response({"token": token.key})

    @extend_schema(tags=['Management UI'])
    @action(
        detail=True, methods=['post'], url_path='force_reenroll',
        permission_classes=ADMIN_ONLY,
    )
    def force_reenroll(self, request, pk=None):
        """Revoke token so the worker re-enrolls on next heartbeat."""
        worker, err = self._require_worker_with_user(pk)
        if err:
            return err
        Token.objects.filter(user=worker.user).delete()
        logger.info(
            "Forced re-enrollment for worker %s by user id=%s",
            worker.hostname, request.user.pk,
        )
        return Response({"detail": "Token revoked. Worker will re-enroll."})
