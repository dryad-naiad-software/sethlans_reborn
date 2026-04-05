# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shutdown API view: allows admin users to gracefully shut down the
Django manager process from the Angular frontend.
"""

import logging
import os
import signal
import threading

from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from ..permissions import IsAdmin

logger = logging.getLogger(__name__)


@extend_schema(tags=['System'])
@api_view(['POST'])
@permission_classes([IsAdmin])
def shutdown_view(request):
    """
    Trigger a graceful shutdown of the Django manager process.

    Sends SIGINT to the current process after a short delay so the
    HTTP response can be delivered to the client first.  This is
    equivalent to the user pressing Ctrl+C in the terminal.
    """
    logger.info(
        "Shutdown requested by user '%s'.",
        request.user.username,
    )

    def _delayed_shutdown():
        logger.info("Sending SIGINT to manager process (pid=%d).", os.getpid())
        os.kill(os.getpid(), signal.SIGINT)

    threading.Timer(0.5, _delayed_shutdown).start()

    return Response({"status": "shutting_down"})
