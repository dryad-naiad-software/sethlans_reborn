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

__all__ = [
    "ProjectViewSet",
    "WorkerHeartbeatViewSet",
    "AnimationViewSet",
    "TiledJobViewSet",
    "AssetViewSet",
    "JobViewSet",
    "dashboard_stats",
]
