# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Issue #220: worker token actions are admin-only.

``POST /api/heartbeat/{pk}/revoke_token/``, ``/regenerate_token/`` and
``/force_reenroll/`` used to inherit the heartbeat viewset's
``IsAuthenticated`` permission, so worker A's API token could rotate
worker B's token (and receive B's new key) or revoke every other
worker's token.  They now require ``IsAdminNotWorker`` (staff, never a
worker account), set per action.
"""

from unittest import mock

import pytest
from rest_framework.test import APIClient

from workers.permissions import IsAdminNotWorker
from workers.views.heartbeat import WorkerHeartbeatViewSet

from .token_admin_helpers import (
    HEARTBEAT_URL,
    TOKEN_ACTIONS,
    action_url,
    session_client,
    token_key,
)


@pytest.fixture
def two_workers(make_worker_client):
    """Two enrolled workers: ``(worker_a, client_a, token_a, worker_b, token_b)``."""
    worker_a, client_a, token_a = make_worker_client('token-host-a')
    worker_b, _client_b, token_b = make_worker_client('token-host-b')
    return worker_a, client_a, token_a, worker_b, token_b


@pytest.mark.django_db
class TestWorkerTokenCannotUseTokenActions:
    """A worker's API token gets 403 and changes no token."""

    @pytest.mark.parametrize('action_name', TOKEN_ACTIONS)
    def test_worker_token_against_other_worker_is_forbidden(
        self, two_workers, action_name,
    ):
        """Worker A cannot revoke/rotate/re-enroll worker B."""
        worker_a, client_a, token_a, worker_b, token_b = two_workers

        resp = client_a.post(action_url(worker_b, action_name))

        assert resp.status_code == 403
        assert 'token' not in resp.data
        assert token_key(worker_b) == token_b.key
        assert token_key(worker_a) == token_a.key

    @pytest.mark.parametrize('action_name', TOKEN_ACTIONS)
    def test_worker_token_against_itself_is_forbidden(
        self, two_workers, action_name,
    ):
        """Worker A cannot call the admin actions on its own row either."""
        worker_a, client_a, token_a, worker_b, token_b = two_workers

        resp = client_a.post(action_url(worker_a, action_name))

        assert resp.status_code == 403
        assert 'token' not in resp.data
        assert token_key(worker_a) == token_a.key
        assert token_key(worker_b) == token_b.key

    def test_worker_token_still_heartbeats_after_rejection(
        self, two_workers, default_version,
    ):
        """The 403s leave both workers' credentials usable."""
        worker_a, client_a, _token_a, worker_b, _token_b = two_workers
        for action_name in TOKEN_ACTIONS:
            client_a.post(action_url(worker_b, action_name))

        resp = client_a.post(
            HEARTBEAT_URL,
            data={'hostname': worker_a.hostname},
            format='json',
        )
        assert resp.status_code == 200


@pytest.mark.django_db
class TestNonAdminCallersAreRejected:
    """Non-staff sessions get 403; anonymous callers get 401."""

    @pytest.mark.parametrize('action_name', TOKEN_ACTIONS)
    def test_non_admin_session_user_is_forbidden(
        self, two_workers, action_name,
    ):
        """A logged-in, non-staff user cannot use the token actions."""
        _worker_a, _client_a, _token_a, worker_b, token_b = two_workers
        client = session_client('plain-user', 'pw-plain-123', False)

        resp = client.post(action_url(worker_b, action_name))

        assert resp.status_code == 403
        assert token_key(worker_b) == token_b.key

    @pytest.mark.parametrize('action_name', TOKEN_ACTIONS)
    def test_anonymous_caller_is_unauthorized(self, two_workers, action_name):
        """No credentials at all is a 401 and changes nothing."""
        _worker_a, _client_a, _token_a, worker_b, token_b = two_workers

        resp = APIClient().post(action_url(worker_b, action_name))

        assert resp.status_code == 401
        assert token_key(worker_b) == token_b.key


@pytest.mark.django_db
class TestAdminSessionStillWorks:
    """A staff session (the Angular dashboard path) keeps full access."""

    @pytest.fixture
    def admin_session(self, db):
        return session_client('staff-admin', 'pw-staff-123', True)

    def test_admin_can_revoke_token(self, two_workers, admin_session):
        worker_a, _client_a, token_a, worker_b, _token_b = two_workers

        resp = admin_session.post(action_url(worker_b, 'revoke_token'))

        assert resp.status_code == 200
        assert token_key(worker_b) is None
        assert token_key(worker_a) == token_a.key

    def test_admin_can_regenerate_token(self, two_workers, admin_session):
        _worker_a, _client_a, _token_a, worker_b, token_b = two_workers

        resp = admin_session.post(action_url(worker_b, 'regenerate_token'))

        assert resp.status_code == 200
        new_key = resp.data['token']
        assert new_key != token_b.key
        assert token_key(worker_b) == new_key

    def test_admin_can_force_reenroll(self, two_workers, admin_session):
        _worker_a, _client_a, _token_a, worker_b, _token_b = two_workers

        resp = admin_session.post(action_url(worker_b, 'force_reenroll'))

        assert resp.status_code == 200
        assert token_key(worker_b) is None

    def test_admin_regenerated_token_authenticates_worker(
        self, two_workers, admin_session, default_version,
    ):
        """The rotated key is a working credential for worker B."""
        _worker_a, _client_a, _token_a, worker_b, _token_b = two_workers
        new_key = admin_session.post(
            action_url(worker_b, 'regenerate_token'),
        ).data['token']

        client_b = APIClient()
        client_b.credentials(HTTP_AUTHORIZATION=f'Token {new_key}')
        resp = client_b.post(
            HEARTBEAT_URL,
            data={'hostname': worker_b.hostname},
            format='json',
        )
        assert resp.status_code == 200

    def test_regenerate_token_is_audit_logged(
        self, two_workers, admin_session,
    ):
        """``regenerate_token`` logs the worker and the acting user id."""
        _worker_a, _client_a, _token_a, worker_b, _token_b = two_workers
        admin_id = admin_session.session['_auth_user_id']

        # caplog does not see ``workers.*`` records; patch the logger.
        with mock.patch('workers.views.token_actions.logger') as log:
            resp = admin_session.post(
                action_url(worker_b, 'regenerate_token'),
            )

        assert resp.status_code == 200
        log.info.assert_called_once_with(
            "Token regenerated for worker %s by user id=%s",
            worker_b.hostname, int(admin_id),
        )


class TestTokenActionsDeclareAdminPermission:
    """Structural guard: each token action carries its own permission."""

    @pytest.mark.parametrize('action_name', TOKEN_ACTIONS)
    def test_action_permission_classes_are_admin_only(self, action_name):
        method = getattr(WorkerHeartbeatViewSet, action_name)
        assert method.kwargs['permission_classes'] == [IsAdminNotWorker]
