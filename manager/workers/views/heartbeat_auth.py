# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Caller binding for ``POST /api/heartbeat/`` (issue #215).

A heartbeat (or full re-registration) may only touch the ``Worker`` row
that belongs to the authenticated caller.  The worker is identified by
the token's user (``request.user.worker_profile``), never by the
``hostname`` in the request body.  The body hostname is only checked
against that row so a mismatch is rejected instead of silently acting
on (or creating) a different worker.

New workers are created exclusively by ``POST /api/enroll/`` (or the
in-process ``services.auto_enroll`` path), which creates the ``User``,
``Token`` and ``Worker`` row together.  The heartbeat endpoint never
creates any of them.

Renamed machines: the worker agent always reports
``socket.gethostname()``.  If the machine is renamed after enrollment,
the reported hostname no longer matches the enrolled row and every
heartbeat is rejected with 403 and a message telling the operator to
re-enroll.  The worker agent retries registration on a fixed 30-second
back-off and periodic heartbeats on the heartbeat interval, so a 403
never produces a hot loop.
"""

import logging
from collections.abc import Mapping

from rest_framework import status
from rest_framework.response import Response

from ..permissions import IsWorker

logger = logging.getLogger(__name__)

# Upper bound on how much of an untrusted hostname is written to the log.
_MAX_LOGGED_HOSTNAME = 255

NOT_A_WORKER_DETAIL = (
    "Heartbeat requires a worker API token. Enroll this worker via "
    "POST /api/enroll/ first."
)
HOSTNAME_MISMATCH_DETAIL = (
    "The reported hostname does not match the worker this API token "
    "was issued to. If this machine was renamed, re-enroll it with the "
    "manager's enrollment key."
)


def _forbidden(detail):
    """Return a 403 Response with *detail*."""
    return Response({"detail": detail}, status=status.HTTP_403_FORBIDDEN)


def bind_heartbeat_caller(request, view):
    """Resolve the caller's own ``Worker`` for a heartbeat request.

    Returns ``(worker, None)`` on success or ``(None, Response)`` when
    the request must be rejected:

    * 403 if the caller is not a token-authenticated worker user (an
      admin or dashboard session, or a token whose user has no linked
      ``Worker``);
    * 400 if the body is not a JSON object or has no ``hostname``;
    * 403 if the body ``hostname`` is not the caller's own worker.
    """
    if not IsWorker().has_permission(request, view):
        logger.warning(
            "Heartbeat rejected: caller user id=%s is not a "
            "token-authenticated worker.",
            getattr(request.user, 'pk', None),
        )
        return None, _forbidden(NOT_A_WORKER_DETAIL)

    if not isinstance(request.data, Mapping):
        return None, Response(
            {"detail": "Expected a JSON object."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    worker = request.user.worker_profile
    hostname = request.data.get('hostname')
    if not hostname:
        return None, Response(
            {"detail": "Hostname is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if hostname != worker.hostname:
        logger.warning(
            "Heartbeat rejected: token for worker id=%s (%s) reported "
            "hostname %r.",
            worker.pk,
            worker.hostname,
            str(hostname)[:_MAX_LOGGED_HOSTNAME],
        )
        return None, _forbidden(HOSTNAME_MISMATCH_DETAIL)

    return worker, None
