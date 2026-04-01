# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Custom permission classes for the Sethlans manager API.

- IsAdmin: session-authenticated staff users (Angular UI).
- IsWorker: token-authenticated worker agents.
- IsAdminOrWorkerReadOnly: full access for admins, read-only for workers.
"""

from rest_framework.authtoken.models import Token
from rest_framework.permissions import BasePermission, SAFE_METHODS


class IsAdmin(BasePermission):
    """
    Grants access to authenticated staff users (admin via session auth).
    """

    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.is_staff
        )


class IsWorker(BasePermission):
    """
    Grants access to token-authenticated worker agents.

    Checks that the request uses token auth AND that the user has a
    linked worker_profile (i.e., the token belongs to a worker user,
    not an admin who happens to have a token).
    """

    def has_permission(self, request, view):
        return (
            isinstance(request.auth, Token)
            and hasattr(request.user, 'worker_profile')
        )


class IsAdminOrWorkerReadOnly(BasePermission):
    """
    Full access for admin users; read-only access for worker agents.
    """

    def has_permission(self, request, view):
        if IsAdmin().has_permission(request, view):
            return True
        if request.method in SAFE_METHODS:
            return IsWorker().has_permission(request, view)
        return False
