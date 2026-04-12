# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for ``POST /api/heartbeat/`` after the legacy
``X-Enrollment-Key`` path was removed (spec FR-19).

This file used to exercise the old combined endpoint where an unknown
worker could send ``X-Enrollment-Key`` on the heartbeat URL and get
registered in-place.  That branch has been deleted — workers must now
call ``POST /api/enroll/`` first to obtain a token, then use that token
on every subsequent heartbeat.  The tests below are the regression
suite that the old path is **truly gone**: any request without a valid
``Authorization: Token ...`` header now returns 401, and the legacy
header has no effect whatsoever.

The spec-mandated structural AC-21a check (no ``get_permissions`` /
``get_authenticators`` instance overrides) lives alongside the
enrollment endpoint tests in ``test_enroll_endpoint.py``.
"""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from workers.models import Worker

User = get_user_model()

HEARTBEAT_URL = '/api/heartbeat/'


@pytest.mark.django_db
class TestHeartbeatRequiresToken:
    """FR-19 / AC-21: heartbeat now demands ``TokenAuthentication``."""

    def test_no_credentials_returns_401(self, default_version):
        """A request with no Authorization header returns 401."""
        client = APIClient()
        resp = client.post(
            HEARTBEAT_URL,
            data={'hostname': 'unknown-worker'},
            format='json',
        )
        assert resp.status_code == 401

    def test_full_registration_payload_without_token_returns_401(
        self, default_version,
    ):
        """A full-registration body without a token still returns 401.

        The old path accepted an ``os``-bearing payload as an enrollment
        trigger; that branch is gone.
        """
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
        )
        assert resp.status_code == 401

    def test_legacy_enrollment_key_header_is_ignored(
        self, default_version,
    ):
        """The ``X-Enrollment-Key`` header is no longer honoured.

        This is the FR-19 "no backward compat" regression.  The header
        is preserved at send-time as a deliberate foot-gun test — the
        server MUST NOT recognise it.
        """
        client = APIClient()
        resp = client.post(
            HEARTBEAT_URL,
            data={
                'hostname': 'legacy-worker',
                'os': 'Linux',
                'available_tools': {},
            },
            format='json',
            HTTP_X_ENROLLMENT_KEY='anything-goes-here',
        )
        assert resp.status_code == 401
        # And crucially: no Worker or User was conjured into existence.
        assert not Worker.objects.filter(hostname='legacy-worker').exists()
        assert not User.objects.filter(
            username='worker_legacy-worker',
        ).exists()

    def test_invalid_token_returns_401(self, default_version):
        """An unknown token value returns 401, not 403."""
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Token not-a-real-token')
        resp = client.post(
            HEARTBEAT_URL,
            data={'hostname': 'inthost'},
            format='json',
        )
        assert resp.status_code == 401


@pytest.mark.django_db
class TestTokenAuthenticatedHeartbeat:
    """Existing-worker heartbeats and the blender-version response."""

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
        """Heartbeat response includes ``required_blender_versions``."""
        worker, client = worker_with_token
        resp = client.post(
            HEARTBEAT_URL,
            data={'hostname': worker.hostname},
            format='json',
        )
        assert resp.status_code == 200
        assert 'required_blender_versions' in resp.data
        versions = resp.data['required_blender_versions']
        assert len(versions) >= 1
        assert any(v['series'] == '4.2' for v in versions)


@pytest.mark.django_db
class TestTokenRevocation:
    """After token revocation, the worker can no longer heartbeat."""

    def test_revoke_then_heartbeat_returns_401(
        self, admin_client, worker_with_token, default_version,
    ):
        """After revoke_token, further heartbeats with the dead token 401."""
        worker, worker_client = worker_with_token

        # Admin revokes the token.
        resp = admin_client.post(
            f'{HEARTBEAT_URL}{worker.pk}/revoke_token/',
        )
        assert resp.status_code == 200

        # The token row is gone, so subsequent heartbeats 401.
        assert not Token.objects.filter(user=worker.user).exists()
        resp = worker_client.post(
            HEARTBEAT_URL,
            data={'hostname': worker.hostname},
            format='json',
        )
        assert resp.status_code == 401
