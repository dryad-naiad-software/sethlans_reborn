# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Authentication API views: CSRF bootstrap, login, logout, user info,
and enrollment key regeneration.
"""

import configparser
import logging
import os
import tempfile

from django.contrib.auth import authenticate, login, logout
from django.views.decorators.csrf import ensure_csrf_cookie
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from ..permissions import IsAdmin
from ..rate_limiter import InMemoryRateLimiter

logger = logging.getLogger(__name__)

# Module-level rate limiter: 10 failed attempts per IP in 5 minutes
_login_rate_limiter = InMemoryRateLimiter(
    max_attempts=10, window_seconds=300
)


def _get_client_ip(request):
    """Extract client IP from the request."""
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


@extend_schema(tags=['Auth'])
@api_view(['GET'])
@authentication_classes([])
@permission_classes([AllowAny])
@ensure_csrf_cookie
def csrf_view(request):
    """
    Bootstrap the CSRF cookie for the Angular app.

    The @ensure_csrf_cookie decorator forces Django to set the
    csrftoken cookie even on a GET request with no form.
    """
    return Response({"detail": "CSRF cookie set."})


@extend_schema(tags=['Auth'])
@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@ensure_csrf_cookie
def login_view(request):
    """
    Authenticate via username/password and create a session.

    Rate-limited: 10 failed attempts per IP within 5 minutes.
    """
    ip = _get_client_ip(request)
    if _login_rate_limiter.is_rate_limited(ip):
        return Response(
            {"detail": "Too many login attempts. Try again later."},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    username = request.data.get('username')
    password = request.data.get('password')

    user = authenticate(request, username=username, password=password)
    if user is not None:
        login(request, user)
        return Response({
            "username": user.username,
            "is_staff": user.is_staff,
        })

    _login_rate_limiter.record_attempt(ip)
    return Response(
        {"detail": "Invalid credentials."},
        status=status.HTTP_401_UNAUTHORIZED,
    )


@extend_schema(tags=['Auth'])
@api_view(['POST'])
@permission_classes([IsAdmin])
def logout_view(request):
    """Destroy the current session."""
    logout(request)
    return Response({"detail": "Logged out."})


@extend_schema(tags=['Auth'])
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def user_view(request):
    """Return the current authenticated user's info."""
    return Response({
        "username": request.user.username,
        "is_staff": request.user.is_staff,
    })


@extend_schema(tags=['Auth'])
@api_view(['POST'])
@permission_classes([IsAdmin])
def regenerate_enrollment_key_view(request):
    """
    Generate a new enrollment key and write it to manager.ini.

    Does NOT mutate django.conf.settings at runtime; the heartbeat
    view re-reads from the config file on each enrollment attempt.
    """
    import secrets
    from django.conf import settings as django_settings

    new_key = secrets.token_urlsafe(32)
    config_path = django_settings.BASE_DIR / 'manager.ini'

    config = configparser.ConfigParser()
    if config_path.exists():
        config.read(config_path)
    if not config.has_section('security'):
        config.add_section('security')
    config.set('security', 'enrollment_key', new_key)

    # Atomic write: write to temp file, then replace
    config_dir = os.path.dirname(config_path)
    fd, tmp_path = tempfile.mkstemp(dir=config_dir, suffix='.ini')
    try:
        with os.fdopen(fd, 'w') as f:
            config.write(f)
        os.replace(tmp_path, config_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

    logger.info("Enrollment key regenerated.")
    return Response({"enrollment_key": new_key})
