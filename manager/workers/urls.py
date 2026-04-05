# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    WorkerHeartbeatViewSet, JobViewSet, AnimationViewSet,
    AssetViewSet, ProjectViewSet, TiledJobViewSet,
    SupportedBlenderVersionViewSet, dashboard_stats,
    csrf_view, login_view, logout_view, user_view,
    regenerate_enrollment_key_view, shutdown_view,
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
router.register(r'supported-versions', SupportedBlenderVersionViewSet, basename='supportedversion')

urlpatterns = [
    path('stats/', dashboard_stats, name='dashboard-stats'),
    # System endpoints
    path('system/shutdown/', shutdown_view, name='system-shutdown'),
    # Auth endpoints
    path('auth/csrf/', csrf_view, name='auth-csrf'),
    path('auth/login/', login_view, name='auth-login'),
    path('auth/logout/', logout_view, name='auth-logout'),
    path('auth/user/', user_view, name='auth-user'),
    path(
        'auth/regenerate_enrollment_key/',
        regenerate_enrollment_key_view,
        name='auth-regenerate-enrollment-key',
    ),
    # Include all router URLs at the root of /api/
    path('', include(router.urls)),
]
