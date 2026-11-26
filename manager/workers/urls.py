# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

from django.urls import include, path, register_converter
from rest_framework.routers import DefaultRouter

from .url_converters import BlenderPlatformConverter, BlenderVersionConverter
from .views.blender_builds import BlenderBuildArchiveView, BlenderBuildView
from .views import (
    WorkerHeartbeatViewSet, JobViewSet, AnimationViewSet,
    AssetViewSet, ProjectViewSet, QueueSettingViewSet,
    TiledJobViewSet,
    SupportedBlenderVersionViewSet, dashboard_stats,
    csrf_view, login_view, logout_view, user_view,
    regenerate_enrollment_key_view, enroll_view,
    health_view, shutdown_view,
    FFmpegStatusView,
)
from .views.manager_defaults import manager_defaults_view
from .views.manager_summary import manager_summary_view
from .views.sequence_discoveries import SequenceDiscoveryViewSet
from .views.sequence_submissions import SequenceSubmissionViewSet
from .views.sethlans_share import SethlansShareView

# BS-MGR-12: only a full version and the six platform ids reach the views.
register_converter(BlenderVersionConverter, 'blender_version')
register_converter(BlenderPlatformConverter, 'blender_platform')

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
router.register(r'sequence-discoveries', SequenceDiscoveryViewSet, basename='sequence-discovery')
router.register(r'sequence-submissions', SequenceSubmissionViewSet, basename='sequence-submission')

urlpatterns = [
    path('stats/', dashboard_stats, name='dashboard-stats'),
    # System endpoints
    path('health/', health_view, name='health'),
    path('system/shutdown/', shutdown_view, name='system-shutdown'),
    # FFmpeg / video-assembly status (role-aware payload).
    path(
        'ffmpeg-status/',
        FFmpegStatusView.as_view(),
        name='ffmpeg-status',
    ),
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
    # The manager's Sethlans share setting (Unreal FR-MS-2).
    path('sethlans-share/', SethlansShareView.as_view(), name='sethlans-share'),
    # Cached Blender builds for enrolled workers (BS-MGR-10, BS-MGR-11).
    path(
        'blender-builds/<blender_version:version>/<blender_platform:platform>/',
        BlenderBuildView.as_view(),
        name='blender-build',
    ),
    path(
        'blender-builds/<blender_version:version>/<blender_platform:platform>/archive/',
        BlenderBuildArchiveView.as_view(),
        name='blender-build-archive',
    ),
    # Include all router URLs at the root of /api/
    path('', include(router.urls)),
]
