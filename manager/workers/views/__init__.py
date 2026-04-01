# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
# Created by Mario Estrella on 07/22/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
# workers/views/__init__.py

from .projects import ProjectViewSet
from .heartbeat import WorkerHeartbeatViewSet
from .animations import AnimationViewSet
from .tiled_jobs import TiledJobViewSet
from .assets import AssetViewSet
from .jobs import JobViewSet
from .stats import dashboard_stats
from .auth import (
    csrf_view,
    login_view,
    logout_view,
    user_view,
    regenerate_enrollment_key_view,
)

__all__ = [
    "ProjectViewSet",
    "WorkerHeartbeatViewSet",
    "AnimationViewSet",
    "TiledJobViewSet",
    "AssetViewSet",
    "JobViewSet",
    "dashboard_stats",
    "csrf_view",
    "login_view",
    "logout_view",
    "user_view",
    "regenerate_enrollment_key_view",
]
