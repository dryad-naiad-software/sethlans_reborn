# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Authentication API views: CSRF bootstrap, login, logout, user info,
and enrollment key regeneration.
"""

import logging

from django.contrib.auth import authenticate, login, logout
from django.views.decorators.csrf import ensure_csrf_cookie
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from .. import enrollment_key
from ..permissions import IsAdmin
from ..rate_limiter import InMemoryRateLimiter
from ._helpers import client_ip as _get_client_ip

logger = logging.getLogger(__name__)

# Module-level rate limiter: 10 failed attempts per IP in 5 minutes
_login_rate_limiter = InMemoryRateLimiter(
    max_attempts=10, window_seconds=300
)


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


@extend_schema(
    tags=['Worker Enrollment'],
    responses={
        200: OpenApiResponse(
            description="New enrollment key in hyphenated display form.",
        ),
        401: OpenApiResponse(description="Unauthenticated"),
        403: OpenApiResponse(description="Not an administrator"),
    },
    description=(
        "Rotate the shared worker enrollment key.  Writes the new key "
        "to the ManagerSettings singleton row and returns the hyphenated "
        "display form.  Admin-only."
    ),
)
@api_view(['POST'])
@permission_classes([IsAdmin])
def regenerate_enrollment_key_view(request):
    """Rotate the shared enrollment key and return the new display form.

    The DB row is the authoritative store — ``manager.ini`` is not touched.
    Existing per-worker tokens continue to work; only new enrollments
    need the new key.
    """
    display_key = enrollment_key.rotate()
    logger.info("Enrollment key regenerated.")
    return Response({"enrollment_key": display_key})
