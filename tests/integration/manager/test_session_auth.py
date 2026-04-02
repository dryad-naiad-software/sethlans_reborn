# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for session-based authentication.

Covers: CSRF cookie, login with valid/invalid credentials,
user info endpoint, and logout.
"""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

User = get_user_model()

CSRF_URL = '/api/auth/csrf/'
LOGIN_URL = '/api/auth/login/'
LOGOUT_URL = '/api/auth/logout/'
USER_URL = '/api/auth/user/'


@pytest.fixture(autouse=True)
def _reset_login_rate_limiter():
    """Clear the login rate limiter between tests."""
    from workers.views.auth import _login_rate_limiter
    with _login_rate_limiter._lock:
        _login_rate_limiter._attempts.clear()


@pytest.mark.django_db
class TestCsrfEndpoint:

    def test_csrf_endpoint_sets_cookie(self):
        """GET /api/auth/csrf/ sets csrftoken cookie."""
        client = APIClient(enforce_csrf_checks=True)
        resp = client.get(CSRF_URL)
        assert resp.status_code == 200
        assert 'csrftoken' in resp.cookies


@pytest.mark.django_db
class TestLogin:

    def test_login_valid_credentials(self, admin_user):
        """Login with valid credentials returns 200."""
        client = APIClient()
        resp = client.post(
            LOGIN_URL,
            data={
                'username': 'admin',
                'password': 'testpass123',
            },
            format='json',
        )
        assert resp.status_code == 200
        assert resp.data['username'] == 'admin'
        assert resp.data['is_staff'] is True

    def test_login_invalid_credentials(self, admin_user):
        """Login with wrong password returns 401."""
        client = APIClient()
        resp = client.post(
            LOGIN_URL,
            data={
                'username': 'admin',
                'password': 'wrongpassword',
            },
            format='json',
        )
        assert resp.status_code == 401
        assert 'Invalid' in resp.data['detail']

    def test_login_nonexistent_user(self):
        """Login with nonexistent user returns 401."""
        client = APIClient()
        resp = client.post(
            LOGIN_URL,
            data={
                'username': 'nobody',
                'password': 'whatever',
            },
            format='json',
        )
        assert resp.status_code == 401


@pytest.mark.django_db
class TestUserInfo:

    def test_user_info_returns_current_user(self, admin_user):
        """Authenticated user info endpoint returns user data."""
        client = APIClient()
        client.force_authenticate(user=admin_user)
        resp = client.get(USER_URL)
        assert resp.status_code == 200
        assert resp.data['username'] == 'admin'
        assert resp.data['is_staff'] is True

    def test_user_info_unauthenticated_returns_403(self):
        """Unauthenticated request to user info returns 401/403."""
        client = APIClient()
        resp = client.get(USER_URL)
        assert resp.status_code in (401, 403)


@pytest.mark.django_db
class TestLogout:

    def test_logout_destroys_session(self, admin_user):
        """Logout endpoint destroys the session."""
        client = APIClient()
        # Login first
        client.post(
            LOGIN_URL,
            data={
                'username': 'admin',
                'password': 'testpass123',
            },
            format='json',
        )

        # Logout
        resp = client.post(LOGOUT_URL)
        assert resp.status_code == 200
        assert resp.data['detail'] == 'Logged out.'

        # Verify session is gone: user info should fail
        resp = client.get(USER_URL)
        assert resp.status_code in (401, 403)
