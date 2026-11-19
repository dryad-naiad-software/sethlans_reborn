# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Admin card actions on a worker (worker card selection FR-CR-5).

``POST /api/heartbeat/{pk}/dismiss-card-alert/`` with ``{"alert_id"}``
adds the id to ``Worker.card_alert_dismissals``; the next heartbeat
answer delivers it (``heartbeat_card_sync``) and the worker removes the
alert, which can return a held card to use (O8).  Admin-only, as the
token actions (``IsAdminNotWorker``): a worker token gets 403 and an
anonymous caller 401.  The id must name an alert the worker reported, so
the action cannot invent an alert; knowing an id (the worker's card page
is anonymous) is never permission.  The write happens under the row lock
of D24.  Every non-2xx answer is ``{"detail": "<sentence>"}`` (R6).

Chunk 11 adds the card-choice request and withdraw actions here
(FR-CM-10, FR-CM-11), with their checks in ``services/card_choice.py``.
"""

import logging
import re
from collections.abc import Mapping

from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from ..models import Worker
from ..serializers.worker_card_schemas import (
    DetailResponse, DismissCardAlertRequest, DismissCardAlertResponse,
)
from ..services.row_locks import lock_own_rows
from .heartbeat_card_sync import alert_ids, pruned_dismissals
from .heartbeat_cards import MAX_ENTRIES, is_alert_id
from .token_actions import ADMIN_ONLY

logger = logging.getLogger(__name__)

BAD_ID_DETAIL = "alert_id must be 32 lower-case hexadecimal characters."
NOT_REPORTED_DETAIL = "This worker has not reported an alert with that id."
NOT_FOUND_DETAIL = "Worker not found."
# A URL pk that cannot be a worker id is a 404, never a database error.
_WORKER_PK = re.compile(r'[0-9]{1,12}', re.ASCII)


def _detail(code, sentence):
    return Response({"detail": sentence}, status=code)


def dismiss_alert(pk, alert_id):
    """Append ``alert_id`` under the row lock; return ``(worker, error sentence)``.

    ``worker`` is ``None`` for an unknown worker.  Ids that no longer name
    a stored alert are pruned in the same write, so the list stays within
    the 16 alerts a report can hold.
    """
    with transaction.atomic():
        worker = lock_own_rows(Worker.objects).filter(pk=pk).first()
        if worker is None:
            return None, NOT_FOUND_DETAIL
        if alert_id not in alert_ids(worker.card_status):
            return worker, NOT_REPORTED_DETAIL
        dismissals = pruned_dismissals(worker.card_alert_dismissals, worker.card_status)
        if alert_id not in dismissals:
            dismissals.append(alert_id)
        dismissals = dismissals[-MAX_ENTRIES:]
        if dismissals != worker.card_alert_dismissals:
            worker.card_alert_dismissals = dismissals
            worker.save(update_fields=['card_alert_dismissals'])
        return worker, None


class WorkerCardActionsMixin:
    """Mixin providing the admin-only card actions for WorkerHeartbeatViewSet."""

    @extend_schema(
        tags=['Management UI'],
        request=DismissCardAlertRequest,
        responses={
            200: DismissCardAlertResponse, 400: DetailResponse, 401: DetailResponse,
            403: DetailResponse, 404: DetailResponse,
        },
    )
    @action(
        detail=True, methods=['post'], url_path='dismiss-card-alert',
        permission_classes=ADMIN_ONLY,
    )
    def dismiss_card_alert(self, request, pk=None):
        """Dismiss a card alert the worker reported; the worker applies it next heartbeat."""
        data = request.data
        alert_id = data.get('alert_id') if isinstance(data, Mapping) else None
        if not is_alert_id(alert_id):
            return _detail(status.HTTP_400_BAD_REQUEST, BAD_ID_DETAIL)
        if not _WORKER_PK.fullmatch(str(pk)):
            return _detail(status.HTTP_404_NOT_FOUND, NOT_FOUND_DETAIL)
        worker, error = dismiss_alert(int(pk), alert_id)
        if worker is None:
            return _detail(status.HTTP_404_NOT_FOUND, error)
        if error is not None:
            return _detail(status.HTTP_400_BAD_REQUEST, error)
        logger.info(
            "Card alert %s on worker %s dismissed by user id=%s",
            alert_id, worker.hostname, request.user.pk,
        )
        return Response({
            "card_status": worker.card_status or None,
            "card_alert_dismissals": list(worker.card_alert_dismissals),
        })
