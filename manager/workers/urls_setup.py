# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
URL patterns for the setup wizard API (``/api/setup/``).

All endpoints are accessible only while the setup sentinel is absent.
After setup completes, each view returns HTTP 404.
"""

from django.urls import path

from .views.setup_status import (
    setup_status_view,
    setup_topology_view,
    setup_network_view,
)
from .views.setup_database import setup_database_view
from .views.setup_accounts import (
    setup_admin_user_view,
    setup_worker_password_view,
)
from .views.setup_downloads import (
    setup_ffmpeg_start_view,
    setup_ffmpeg_progress_view,
    setup_ffmpeg_cancel_view,
    setup_blender_start_view,
    setup_blender_progress_view,
    setup_blender_cancel_view,
)
from .views.setup_verify import (
    setup_verify_view,
    setup_summary_view,
)
from .views.setup_bootstrap import setup_bootstrap_view
from .views.setup_restart import setup_restart_view

urlpatterns = [
    # Bootstrap: exchange setup token for a setup-phase session cookie.
    path("bootstrap/", setup_bootstrap_view, name="setup-bootstrap"),
    # Restart: create .restart_requested (sentinel required).
    path("restart/", setup_restart_view, name="setup-restart"),
    # FR-A1: Setup status
    path("status/", setup_status_view, name="setup-status"),
    # FR-A2: Topology selection
    path("topology/", setup_topology_view, name="setup-topology"),
    # FR-A3: Network configuration
    path("network/", setup_network_view, name="setup-network"),
    # FR-A4: Database configuration
    path("database/", setup_database_view, name="setup-database"),
    # FR-A5: Admin user creation
    path("admin-user/", setup_admin_user_view, name="setup-admin-user"),
    # FR-A6: Worker UI password
    path(
        "worker-password/",
        setup_worker_password_view,
        name="setup-worker-password",
    ),
    # FR-A7: FFmpeg download start
    path(
        "ffmpeg/start/",
        setup_ffmpeg_start_view,
        name="setup-ffmpeg-start",
    ),
    # FR-A8: FFmpeg download progress
    path(
        "ffmpeg/progress/<str:task_id>/",
        setup_ffmpeg_progress_view,
        name="setup-ffmpeg-progress",
    ),
    # FR-A9: FFmpeg download cancel
    path(
        "ffmpeg/cancel/",
        setup_ffmpeg_cancel_view,
        name="setup-ffmpeg-cancel",
    ),
    # FR-A10: Blender download start
    path(
        "blender/start/",
        setup_blender_start_view,
        name="setup-blender-start",
    ),
    # FR-A11: Blender download progress
    path(
        "blender/progress/<str:task_id>/",
        setup_blender_progress_view,
        name="setup-blender-progress",
    ),
    # FR-A12: Blender download cancel
    path(
        "blender/cancel/",
        setup_blender_cancel_view,
        name="setup-blender-cancel",
    ),
    # FR-A13: Verification
    path("verify/", setup_verify_view, name="setup-verify"),
    # FR-A14: Summary
    path("summary/", setup_summary_view, name="setup-summary"),
]
