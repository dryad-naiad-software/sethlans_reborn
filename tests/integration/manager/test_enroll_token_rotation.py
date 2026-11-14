# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for issue #221: every enrollment rotates the token.

Deleting a ``Worker`` row leaves its ``worker_<host>`` user and that
user's DRF ``Token`` behind.  Re-enrolling the hostname used to hand
the old key back (``Token.objects.get_or_create``), so whoever held it
kept access.  Both enrollment paths (``POST /api/enroll/`` and the
in-process ``auto_enroll_local_worker``) now delete the user's tokens
and issue a new one inside the enrollment transaction.
"""

from unittest import mock

import pytest
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from workers.models import Worker
from workers.services.auto_enroll import auto_enroll_local_worker

from .conftest import TEST_ENROLLMENT_KEY_CANONICAL, fresh_nonce

ENROLL_URL = '/api/enroll/'
HEARTBEAT_URL = '/api/heartbeat/'


def _enroll(hostname):
    """Enroll *hostname* over HTTP and return the issued token key."""
    resp = APIClient().post(
        ENROLL_URL,
        {
            'enrollment_key': TEST_ENROLLMENT_KEY_CANONICAL,
            'hostname': hostname,
            'nonce': fresh_nonce(),
        },
        format='json',
    )
    assert resp.status_code == 200, resp.content
    return resp.json()['payload']['api_token']


def _register(token_key, hostname):
    """POST a registration heartbeat with *token_key*; return the status."""
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token_key}')
    resp = client.post(
        HEARTBEAT_URL,
        data={'hostname': hostname, 'os': 'Linux', 'available_tools': {}},
        format='json',
    )
    return resp.status_code


def _user_id(hostname):
    return Worker.objects.get(hostname=hostname).user_id


@pytest.fixture
def enroll_env(
    seeded_enrollment_key, ready_runtime_state, reset_enroll_module_state,
    default_version,
):
    """Everything ``POST /api/enroll/`` and the heartbeat need."""


@pytest.mark.django_db
class TestEnrollEndpointRotation:
    """``POST /api/enroll/`` never returns a key the user already had."""

    def test_deleted_worker_re_enroll_gets_new_token(self, enroll_env):
        old = _enroll('gone-host')
        old_user = _user_id('gone-host')
        Worker.objects.filter(hostname='gone-host').delete()
        # The orphaned user and its token survive the worker deletion.
        assert Token.objects.filter(key=old).exists()

        new = _enroll('gone-host')

        assert new != old
        assert _user_id('gone-host') == old_user
        assert not Token.objects.filter(key=old).exists()
        assert _register(old, 'gone-host') == 401
        assert _register(new, 'gone-host') == 200

    def test_first_enrollment_issues_one_working_token(self, enroll_env):
        key = _enroll('first-host')

        user_id = _user_id('first-host')
        assert list(
            Token.objects.filter(user_id=user_id).values_list('key', flat=True),
        ) == [key]
        assert _register(key, 'first-host') == 200

    def test_existing_worker_re_enroll_returns_working_token(
        self, enroll_env,
    ):
        """A reinstall of a live worker gets a new, working token."""
        old = _enroll('live-host')
        assert _register(old, 'live-host') == 200

        new = _enroll('live-host')

        assert new != old
        assert Worker.objects.filter(hostname='live-host').count() == 1
        assert Token.objects.filter(user_id=_user_id('live-host')).count() == 1
        assert _register(old, 'live-host') == 401
        assert _register(new, 'live-host') == 200

    def test_failed_rotation_keeps_old_token(self, enroll_env):
        """Rotation shares the enrollment transaction: a failure undoes it."""
        old = _enroll('atomic-host')

        with mock.patch.object(
            Token.objects, 'create', side_effect=RuntimeError('boom'),
        ):
            resp = APIClient().post(
                ENROLL_URL,
                {
                    'enrollment_key': TEST_ENROLLMENT_KEY_CANONICAL,
                    'hostname': 'atomic-host',
                    'nonce': fresh_nonce(),
                },
                format='json',
            )

        assert resp.status_code == 500
        assert Token.objects.filter(key=old).exists()
        assert _register(old, 'atomic-host') == 200


@pytest.mark.django_db
class TestAutoEnrollRotation:
    """``auto_enroll_local_worker`` rotates the token the same way."""

    def test_deleted_worker_auto_enroll_gets_new_token(
        self, ready_runtime_state, default_version,
    ):
        old = auto_enroll_local_worker(hostname='local-gone')['api_token']
        Worker.objects.filter(hostname='local-gone').delete()
        assert Token.objects.filter(key=old).exists()

        new = auto_enroll_local_worker(hostname='local-gone')['api_token']

        assert new != old
        assert not Token.objects.filter(key=old).exists()
        assert _register(old, 'local-gone') == 401
        assert _register(new, 'local-gone') == 200

    def test_existing_worker_auto_enroll_returns_working_token(
        self, ready_runtime_state, default_version,
    ):
        old = auto_enroll_local_worker(hostname='local-live')['api_token']

        new = auto_enroll_local_worker(hostname='local-live')['api_token']

        assert new != old
        assert Token.objects.filter(
            user_id=_user_id('local-live'),
        ).count() == 1
        assert _register(old, 'local-live') == 401
        assert _register(new, 'local-live') == 200

    def test_failed_rotation_keeps_old_token(self, ready_runtime_state):
        old = auto_enroll_local_worker(hostname='local-atomic')['api_token']

        with mock.patch.object(
            Token.objects, 'create', side_effect=RuntimeError('boom'),
        ), pytest.raises(RuntimeError):
            auto_enroll_local_worker(hostname='local-atomic')

        assert Token.objects.filter(key=old).exists()
