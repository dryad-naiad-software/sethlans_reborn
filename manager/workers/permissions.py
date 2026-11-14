# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Custom permission classes for the Sethlans manager API.

- IsAdmin: session-authenticated staff users (Angular UI).
- IsWorker: token-authenticated worker agents.
- IsAdminOrWorkerReadOnly: full access for admins, read-only for workers.
- IsAdminNotWorker: staff users that are not worker agents (issue #220).
- HeartbeatViewSetPermission: viewset-level permission for the heartbeat
  viewset (``list`` admin-only, everything else authenticated).
"""

from rest_framework.authtoken.models import Token
from rest_framework.permissions import (
    BasePermission,
    IsAuthenticated,
    SAFE_METHODS,
)


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


def is_worker_caller(request):
    """Return True if the caller is a worker agent.

    A worker ``Token`` authenticates as its worker ``User``, which
    carries ``worker_profile``; checking the user therefore covers both
    "caller is a worker user" and "caller used a worker Token".  The
    explicit token check guards the (unexpected) case of a Token whose
    user differs from ``request.user``.
    """
    if hasattr(request.user, 'worker_profile'):
        return True
    token = request.auth
    if isinstance(token, Token):
        return hasattr(token.user, 'worker_profile')
    return False


class IsAdminNotWorker(BasePermission):
    """
    Staff users only, never a worker agent (issue #220).

    ``IsAdmin`` checks ``is_staff`` alone.  Worker-credential operations
    (token revoke/rotate, the worker listing) also reject any caller
    linked to a ``Worker`` row, so a worker account that somehow became
    staff still cannot manage other workers' credentials.
    """

    def has_permission(self, request, view):
        return (
            bool(IsAdmin().has_permission(request, view))
            and not is_worker_caller(request)
        )


class HeartbeatViewSetPermission(BasePermission):
    """
    Viewset-level permission for ``WorkerHeartbeatViewSet``.

    ``list`` (``GET /api/heartbeat/``) exposes every worker's IP, OS,
    UI URL, UI cert fingerprint and schedule, so it is admin-only
    (``IsAdminNotWorker``).  Every other action needs an authenticated
    caller; ``create`` and ``yield-event`` then bind to the caller's own
    worker in the view, and the token actions declare their own
    ``permission_classes`` on ``@action``.

    Declared as a class-level ``permission_classes`` entry because the
    viewset must not override ``get_permissions`` (AC-21a, see
    ``tests/integration/manager/test_enroll_rotation.py``).
    """

    ADMIN_ACTIONS = frozenset({'list'})

    def has_permission(self, request, view):
        if getattr(view, 'action', None) in self.ADMIN_ACTIONS:
            return IsAdminNotWorker().has_permission(request, view)
        return IsAuthenticated().has_permission(request, view)
