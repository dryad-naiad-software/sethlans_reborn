# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    WorkerHeartbeatViewSet, JobViewSet, AnimationViewSet,
    AssetViewSet, ProjectViewSet, TiledJobViewSet, dashboard_stats,
)

# Create a router instance
router = DefaultRouter()
# Register your ViewSets with the router.
router.register(r'projects', ProjectViewSet, basename='project')
router.register(r'jobs', JobViewSet, basename='job')
router.register(r'heartbeat', WorkerHeartbeatViewSet, basename='heartbeat')
router.register(r'animations', AnimationViewSet, basename='animation')
router.register(r'assets', AssetViewSet, basename='asset')
router.register(r'tiled-jobs', TiledJobViewSet, basename='tiledjob')

urlpatterns = [
    path('stats/', dashboard_stats, name='dashboard-stats'),
    # Include all router URLs at the root of /api/
    path('', include(router.urls)),
]
