# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
URL configuration served by the manager's loopback-only listener.

The loopback listener binds to ``127.0.0.1:<loopback_port>`` (default
8088) and routes ONLY ``/api/status/public/``.  This keeps the tray's
unauthenticated status endpoint off the public HTTPS listener and
eliminates drift between "registered but reject-by-IP" variants.  See
``development/specs/tray-helper-unified.md`` FR-22 / FR-22a.

The main URLconf (``sethlans_manager.urls``) must NOT register
``/api/status/public/`` — any request to that path on the 0.0.0.0
listener must 404.
"""

from django.urls import path

from workers.views.status_public import status_public_view

urlpatterns = [
    path(
        "api/status/public/",
        status_public_view,
        name="status-public",
    ),
]
