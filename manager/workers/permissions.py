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


class IsSetupPhaseUser(BasePermission):
    """Grants access during the setup wizard via a session-bound flag.

    Passes iff the Django session has ``setup_phase`` set True.  The
    single-writer session_id binding (FR-4a / C3) is enforced separately
    at the top of each mutating setup view via
    ``workers.services.setup_session.enforce_setup_session_binding`` --
    that helper raises ``SetupPhaseError`` so the unified exception
    handler can render it as 409 ``setup_session_conflict`` instead of
    the 403 that a False return from ``has_permission`` would produce.
    """

    message = "Setup-phase session required."

    def has_permission(self, request, view):
        session = getattr(request, "session", None)
        if session is None:
            return False
        return session.get("setup_phase") is True


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
