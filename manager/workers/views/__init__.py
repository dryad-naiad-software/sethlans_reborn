# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

from .projects import ProjectViewSet
from .heartbeat import WorkerHeartbeatViewSet
from .animations import AnimationViewSet
from .tiled_jobs import TiledJobViewSet
from .assets import AssetViewSet
from .jobs import JobViewSet
from .queue_settings import QueueSettingViewSet
from .supported_versions import SupportedBlenderVersionViewSet
from .stats import dashboard_stats
from .auth import (
    csrf_view,
    login_view,
    logout_view,
    user_view,
    regenerate_enrollment_key_view,
)
from .enroll import enroll_view
from .health import health_view
from .shutdown import shutdown_view
from .system_info import system_info_view
from .ffmpeg_status import FFmpegStatusView

__all__ = [
    "ProjectViewSet",
    "WorkerHeartbeatViewSet",
    "AnimationViewSet",
    "TiledJobViewSet",
    "AssetViewSet",
    "JobViewSet",
    "QueueSettingViewSet",
    "SupportedBlenderVersionViewSet",
    "dashboard_stats",
    "csrf_view",
    "login_view",
    "logout_view",
    "user_view",
    "regenerate_enrollment_key_view",
    "enroll_view",
    "health_view",
    "shutdown_view",
    "system_info_view",
    "FFmpegStatusView",
]
