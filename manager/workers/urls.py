# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    WorkerHeartbeatViewSet, JobViewSet, AnimationViewSet,
    AssetViewSet, ProjectViewSet, QueueSettingViewSet,
    TiledJobViewSet,
    SupportedBlenderVersionViewSet, dashboard_stats,
    csrf_view, login_view, logout_view, user_view,
    regenerate_enrollment_key_view, enroll_view,
    health_view, shutdown_view, system_info_view,
)
from .views.manager_defaults import manager_defaults_view
from .views.manager_summary import manager_summary_view

# Create a router instance
router = DefaultRouter()
# Register your ViewSets with the router.
router.register(r'projects', ProjectViewSet, basename='project')
router.register(r'jobs', JobViewSet, basename='job')
router.register(r'heartbeat', WorkerHeartbeatViewSet, basename='heartbeat')
router.register(r'animations', AnimationViewSet, basename='animation')
router.register(r'assets', AssetViewSet, basename='asset')
router.register(r'tiled-jobs', TiledJobViewSet, basename='tiledjob')
router.register(r'queue-settings', QueueSettingViewSet, basename='queuesetting')
router.register(r'supported-versions', SupportedBlenderVersionViewSet, basename='supportedversion')

urlpatterns = [
    path('stats/', dashboard_stats, name='dashboard-stats'),
    # System endpoints
    path('health/', health_view, name='health'),
    path('system/shutdown/', shutdown_view, name='system-shutdown'),
    path('system-info/', system_info_view, name='system-info'),
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
    # Worker enrollment endpoint (HMAC-bootstrapped)
    path('enroll/', enroll_view, name='enroll'),
    # Manager defaults (FR-MA1) — requires TokenAuthentication
    path(
        'manager-defaults/',
        manager_defaults_view,
        name='manager-defaults',
    ),
    # Admin-side manager summary (post-setup; replaces the old
    # setup_summary_view access path after completion).
    path(
        'manager/summary/',
        manager_summary_view,
        name='manager-summary',
    ),
    # Include all router URLs at the root of /api/
    path('', include(router.urls)),
]
