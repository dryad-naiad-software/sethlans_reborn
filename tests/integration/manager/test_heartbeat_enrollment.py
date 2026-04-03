# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for the heartbeat/enrollment endpoint.

Covers: enrollment with valid/invalid key, rate limiting,
token-authenticated heartbeats, required_blender_versions,
and token revocation forcing re-enrollment.
"""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from workers.models import Worker

User = get_user_model()

HEARTBEAT_URL = '/api/heartbeat/'


@pytest.fixture(autouse=True)
def _set_enrollment_key(monkeypatch):
    """Set the enrollment key env var for all tests in this module."""
    monkeypatch.setenv('SETHLANS_SECURITY_ENROLLMENT_KEY', 'test-key-123')


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Clear the enrollment rate limiter between tests."""
    from workers.views.heartbeat import _enrollment_rate_limiter
    with _enrollment_rate_limiter._lock:
        _enrollment_rate_limiter._attempts.clear()


@pytest.mark.django_db
class TestEnrollmentWithValidKey:

    def test_enrollment_creates_worker_user_token(self, default_version):
        """Enrollment with valid key creates Worker, User, Token."""
        client = APIClient()
        resp = client.post(
            HEARTBEAT_URL,
            data={
                'hostname': 'new-worker-01',
                'os': 'Linux',
                'ip_address': '192.168.1.100',
                'available_tools': {'blender': ['4.2.19']},
            },
            format='json',
            HTTP_X_ENROLLMENT_KEY='test-key-123',
        )
        assert resp.status_code == 200
        assert 'token' in resp.data
        assert resp.data['hostname'] == 'new-worker-01'

        # Verify DB objects were created
        worker = Worker.objects.get(hostname='new-worker-01')
        assert worker.user is not None
        assert worker.os == 'Linux'
        assert worker.is_active is True
        assert Token.objects.filter(user=worker.user).exists()

    def test_enrollment_response_includes_blender_versions(
        self, default_version,
    ):
        """Heartbeat response includes required_blender_versions list."""
        client = APIClient()
        resp = client.post(
            HEARTBEAT_URL,
            data={
                'hostname': 'version-check-worker',
                'os': 'Windows',
                'available_tools': {},
            },
            format='json',
            HTTP_X_ENROLLMENT_KEY='test-key-123',
        )
        assert resp.status_code == 200
        versions = resp.data['required_blender_versions']
        assert len(versions) >= 1
        assert any(
            v['series'] == '4.2' for v in versions
        )


@pytest.mark.django_db
class TestEnrollmentWithInvalidKey:

    def test_invalid_key_returns_403(self, default_version):
        """Enrollment with invalid key returns 403."""
        client = APIClient()
        resp = client.post(
            HEARTBEAT_URL,
            data={
                'hostname': 'bad-worker',
                'os': 'Linux',
            },
            format='json',
            HTTP_X_ENROLLMENT_KEY='wrong-key',
        )
        assert resp.status_code == 403
        assert 'Invalid' in resp.data['detail']

    def test_missing_key_returns_403(self, default_version):
        """Enrollment without key header returns 403."""
        client = APIClient()
        resp = client.post(
            HEARTBEAT_URL,
            data={'hostname': 'no-key-worker', 'os': 'Linux'},
            format='json',
        )
        assert resp.status_code == 403


@pytest.mark.django_db
class TestEnrollmentRateLimiting:

    def test_rate_limiting_after_failures(self, default_version):
        """5 failed enrollment attempts then 429."""
        client = APIClient()
        for _ in range(5):
            client.post(
                HEARTBEAT_URL,
                data={'hostname': 'rate-test', 'os': 'Linux'},
                format='json',
                HTTP_X_ENROLLMENT_KEY='wrong-key',
            )

        resp = client.post(
            HEARTBEAT_URL,
            data={'hostname': 'rate-test', 'os': 'Linux'},
            format='json',
            HTTP_X_ENROLLMENT_KEY='test-key-123',
        )
        assert resp.status_code == 429
        assert 'Too many' in resp.data['detail']


@pytest.mark.django_db
class TestTokenAuthenticatedHeartbeat:

    def test_heartbeat_updates_last_seen_and_tools(
        self, worker_with_token,
    ):
        """Token-authenticated heartbeat updates worker fields."""
        worker, client = worker_with_token
        # Backdate last_seen so the heartbeat is guaranteed to advance it,
        # even on fast CI runners where create + heartbeat can share a
        # microsecond timestamp.
        past = worker.last_seen - timedelta(seconds=10)
        Worker.objects.filter(pk=worker.pk).update(last_seen=past)
        worker.refresh_from_db()
        old_last_seen = worker.last_seen

        new_tools = {'blender': ['4.2.19', '4.3.0']}
        resp = client.post(
            HEARTBEAT_URL,
            data={
                'hostname': worker.hostname,
                'available_tools': new_tools,
            },
            format='json',
        )
        assert resp.status_code == 200

        worker.refresh_from_db()
        assert worker.available_tools == new_tools
        assert worker.last_seen > old_last_seen

    def test_heartbeat_response_includes_blender_versions(
        self, worker_with_token, default_version,
    ):
        """Heartbeat response includes required_blender_versions."""
        worker, client = worker_with_token
        resp = client.post(
            HEARTBEAT_URL,
            data={'hostname': worker.hostname},
            format='json',
        )
        assert resp.status_code == 200
        assert 'required_blender_versions' in resp.data


@pytest.mark.django_db
class TestTokenRevocation:

    def test_revoke_then_heartbeat_needs_reenrollment(
        self, admin_client, worker_with_token, default_version,
    ):
        """After token revocation, next heartbeat requires re-enrollment."""
        worker, worker_client = worker_with_token

        # Admin revokes the token
        resp = admin_client.post(
            f'{HEARTBEAT_URL}{worker.pk}/revoke_token/',
        )
        assert resp.status_code == 200

        # Worker tries heartbeat with now-invalid token.
        # DRF TokenAuthentication returns 401 for invalid tokens.
        resp = worker_client.post(
            HEARTBEAT_URL,
            data={'hostname': worker.hostname},
            format='json',
        )
        assert resp.status_code in (401, 403)
