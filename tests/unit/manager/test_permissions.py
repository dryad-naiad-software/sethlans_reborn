# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for manager/workers/permissions.py.

All request/user objects are mocks — no Django DB or auth backend needed.
"""

from unittest.mock import MagicMock

from rest_framework.authtoken.models import Token

from workers.permissions import IsAdmin, IsWorker, IsAdminOrWorkerReadOnly


# ---- IsAdmin ----

class TestIsAdmin:
    def test_grants_access_to_authenticated_staff(
        self, mock_request, mock_view
    ):
        mock_request.user.is_authenticated = True
        mock_request.user.is_staff = True
        assert IsAdmin().has_permission(mock_request, mock_view) is True

    def test_denies_unauthenticated_user(self, mock_request, mock_view):
        mock_request.user.is_authenticated = False
        mock_request.user.is_staff = True
        assert IsAdmin().has_permission(mock_request, mock_view) is False

    def test_denies_non_staff_user(self, mock_request, mock_view):
        mock_request.user.is_authenticated = True
        mock_request.user.is_staff = False
        assert IsAdmin().has_permission(mock_request, mock_view) is False

    def test_denies_anonymous_user(self, mock_view):
        request = MagicMock()
        request.user = None
        assert not IsAdmin().has_permission(request, mock_view)


# ---- IsWorker ----

class TestIsWorker:
    def _make_token_auth(self, request):
        """Set request.auth to a Token-like mock."""
        token = MagicMock(spec=Token)
        request.auth = token

    def test_grants_access_with_token_and_worker_profile(
        self, mock_request, mock_view
    ):
        self._make_token_auth(mock_request)
        mock_request.user.worker_profile = MagicMock()
        assert IsWorker().has_permission(mock_request, mock_view) is True

    def test_denies_token_without_worker_profile(
        self, mock_request, mock_view
    ):
        self._make_token_auth(mock_request)
        # Remove the worker_profile attribute entirely
        del mock_request.user.worker_profile
        assert IsWorker().has_permission(mock_request, mock_view) is False

    def test_denies_session_auth_with_worker_profile(
        self, mock_request, mock_view
    ):
        mock_request.auth = "session_string"  # not a Token instance
        mock_request.user.worker_profile = MagicMock()
        assert IsWorker().has_permission(mock_request, mock_view) is False

    def test_denies_no_auth(self, mock_request, mock_view):
        mock_request.auth = None
        assert IsWorker().has_permission(mock_request, mock_view) is False


# ---- IsAdminOrWorkerReadOnly ----

class TestIsAdminOrWorkerReadOnly:
    def test_admin_gets_full_access_on_post(
        self, mock_request, mock_view
    ):
        mock_request.user.is_authenticated = True
        mock_request.user.is_staff = True
        mock_request.method = "POST"
        perm = IsAdminOrWorkerReadOnly()
        assert perm.has_permission(mock_request, mock_view) is True

    def test_worker_gets_read_on_get(self, mock_request, mock_view):
        mock_request.user.is_authenticated = False
        mock_request.user.is_staff = False
        mock_request.auth = MagicMock(spec=Token)
        mock_request.user.worker_profile = MagicMock()
        mock_request.method = "GET"
        perm = IsAdminOrWorkerReadOnly()
        assert perm.has_permission(mock_request, mock_view) is True

    def test_worker_denied_on_post(self, mock_request, mock_view):
        mock_request.user.is_authenticated = False
        mock_request.user.is_staff = False
        mock_request.auth = MagicMock(spec=Token)
        mock_request.user.worker_profile = MagicMock()
        mock_request.method = "POST"
        perm = IsAdminOrWorkerReadOnly()
        assert perm.has_permission(mock_request, mock_view) is False

    def test_worker_allowed_on_head(self, mock_request, mock_view):
        """HEAD is a safe method."""
        mock_request.user.is_authenticated = False
        mock_request.user.is_staff = False
        mock_request.auth = MagicMock(spec=Token)
        mock_request.user.worker_profile = MagicMock()
        mock_request.method = "HEAD"
        perm = IsAdminOrWorkerReadOnly()
        assert perm.has_permission(mock_request, mock_view) is True

    def test_worker_allowed_on_options(self, mock_request, mock_view):
        mock_request.user.is_authenticated = False
        mock_request.user.is_staff = False
        mock_request.auth = MagicMock(spec=Token)
        mock_request.user.worker_profile = MagicMock()
        mock_request.method = "OPTIONS"
        perm = IsAdminOrWorkerReadOnly()
        assert perm.has_permission(mock_request, mock_view) is True

    def test_worker_denied_on_delete(self, mock_request, mock_view):
        mock_request.user.is_authenticated = False
        mock_request.user.is_staff = False
        mock_request.auth = MagicMock(spec=Token)
        mock_request.user.worker_profile = MagicMock()
        mock_request.method = "DELETE"
        perm = IsAdminOrWorkerReadOnly()
        assert perm.has_permission(mock_request, mock_view) is False

    def test_unauthenticated_no_token_denied(
        self, mock_request, mock_view
    ):
        mock_request.user.is_authenticated = False
        mock_request.user.is_staff = False
        mock_request.auth = None
        mock_request.method = "GET"
        perm = IsAdminOrWorkerReadOnly()
        assert perm.has_permission(mock_request, mock_view) is False
