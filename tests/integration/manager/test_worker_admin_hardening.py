# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Issue #220 hardening beyond the token actions themselves:

* ``GET /api/heartbeat/`` (the worker listing) is admin-only.
* Staff accounts linked to a ``Worker`` are not "admins" for worker
  credential operations (``IsAdminNotWorker``).
* The token actions keep Django's CSRF protection for session callers.
* Enrollment never hands a token for a pre-existing privileged
  ``worker_<hostname>`` account; it falls back to a suffixed username.
"""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from workers.models import Worker
from workers.services.auto_enroll import auto_enroll_local_worker

from .conftest import fresh_nonce
from .token_admin_helpers import (
    CSRF_URL,
    ENROLL_URL,
    HEARTBEAT_URL,
    TOKEN_ACTIONS,
    action_url,
    session_client,
    token_key,
)

User = get_user_model()


@pytest.mark.django_db
class TestWorkerListIsAdminOnly:
    """``GET /api/heartbeat/`` exposes every worker's details: admins only."""

    def test_worker_token_cannot_list(self, make_worker_client):
        make_worker_client('list-host-a')
        _worker, client, _token = make_worker_client('list-host-b')

        resp = client.get(HEARTBEAT_URL)

        assert resp.status_code == 403
        assert not isinstance(resp.data, list)

    def test_non_staff_session_cannot_list(self, make_worker_client):
        make_worker_client('list-host-a')
        client = session_client('plain-lister', 'pw-plain-123', False)

        assert client.get(HEARTBEAT_URL).status_code == 403

    def test_anonymous_cannot_list(self, make_worker_client):
        make_worker_client('list-host-a')

        assert APIClient().get(HEARTBEAT_URL).status_code == 401

    def test_admin_session_lists_workers(self, make_worker_client):
        make_worker_client('list-host-a')
        make_worker_client('list-host-b')
        client = session_client('staff-lister', 'pw-staff-123', True)

        resp = client.get(HEARTBEAT_URL)

        assert resp.status_code == 200
        assert {w['hostname'] for w in resp.data} == {
            'list-host-a', 'list-host-b',
        }


@pytest.mark.django_db
class TestStaffWorkerAccountIsNotAdmin:
    """A staff user linked to a ``Worker`` is still treated as a worker."""

    @pytest.fixture
    def staff_worker(self, make_worker_client):
        """Worker whose user is (wrongly) staff with a usable password."""
        worker, token_client, token = make_worker_client('staff-host')
        worker.user.is_staff = True
        worker.user.set_password('pw-staff-worker-1')
        worker.user.save()
        session = APIClient()
        assert session.login(
            username=worker.user.username, password='pw-staff-worker-1',
        )
        return worker, token_client, session, token

    @pytest.mark.parametrize('use_session', [False, True])
    def test_staff_worker_cannot_list(self, staff_worker, use_session):
        _worker, token_client, session, _token = staff_worker
        client = session if use_session else token_client

        assert client.get(HEARTBEAT_URL).status_code == 403

    @pytest.mark.parametrize('use_session', [False, True])
    @pytest.mark.parametrize('action_name', TOKEN_ACTIONS)
    def test_staff_worker_cannot_use_token_actions(
        self, staff_worker, make_worker_client, action_name, use_session,
    ):
        _worker, token_client, session, _token = staff_worker
        other, _other_client, other_token = make_worker_client('other-host')
        client = session if use_session else token_client

        resp = client.post(action_url(other, action_name))

        assert resp.status_code == 403
        assert token_key(other) == other_token.key


@pytest.mark.django_db
class TestTokenActionsEnforceCsrf:
    """A staff session without a CSRF token cannot drive the actions."""

    @pytest.mark.parametrize('action_name', TOKEN_ACTIONS)
    def test_staff_session_without_csrf_is_forbidden(
        self, make_worker_client, action_name,
    ):
        worker, _client, token = make_worker_client('csrf-host')
        client = session_client(
            'csrf-admin', 'pw-staff-123', True, enforce_csrf=True,
        )

        resp = client.post(action_url(worker, action_name))

        assert resp.status_code == 403
        assert 'CSRF' in str(resp.data.get('detail', ''))
        assert token_key(worker) == token.key

    def test_staff_session_with_csrf_succeeds(self, make_worker_client):
        """The dashboard flow (CSRF cookie + header) still works."""
        worker, _client, token = make_worker_client('csrf-host')
        client = session_client(
            'csrf-admin', 'pw-staff-123', True, enforce_csrf=True,
        )
        client.get(CSRF_URL)
        csrf = client.cookies['csrftoken'].value

        resp = client.post(
            action_url(worker, 'regenerate_token'), HTTP_X_CSRFTOKEN=csrf,
        )

        assert resp.status_code == 200
        assert token_key(worker) == resp.data['token'] != token.key


def _make_operator(kind):
    """Create a pre-existing ``worker_victim-host`` operator account."""
    if kind == 'staff':
        return User.objects.create_user(
            username='worker_victim-host', password='op-pw-123',
            is_staff=True,
        )
    if kind == 'superuser':
        return User.objects.create_superuser(
            username='worker_victim-host', password='op-pw-123',
            email='op@test.com',
        )
    return User.objects.create_user(
        username='worker_victim-host', password='op-pw-123',
    )


def _assert_plain_worker_user(user):
    assert user.username == 'worker_victim-host_1'
    assert not user.is_staff
    assert not user.is_superuser
    assert not user.has_usable_password()


@pytest.mark.django_db
@pytest.mark.usefixtures(
    'seeded_enrollment_key', 'ready_runtime_state', 'reset_enroll_module_state',
)
class TestEnrollmentSkipsPrivilegedUsername:
    """Enrolling hostname X never yields a token for operator ``worker_X``."""

    def _enroll(self, key):
        resp = APIClient().post(
            ENROLL_URL,
            {'enrollment_key': key, 'hostname': 'victim-host',
             'nonce': fresh_nonce()},
            format='json',
        )
        assert resp.status_code == 200
        return Token.objects.get(key=resp.json()['payload']['api_token'])

    @pytest.mark.parametrize('kind', ['staff', 'superuser', 'password'])
    def test_enroll_uses_suffixed_user(self, seeded_enrollment_key, kind):
        operator = _make_operator(kind)

        token = self._enroll(seeded_enrollment_key)

        _assert_plain_worker_user(token.user)
        assert Worker.objects.get(hostname='victim-host').user == token.user
        assert not Token.objects.filter(user=operator).exists()

    def test_enrolled_token_is_not_admin(self, seeded_enrollment_key):
        """The full attack chain: the enrolled token gets 403s."""
        _make_operator('staff')
        token = self._enroll(seeded_enrollment_key)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')

        assert client.get(HEARTBEAT_URL).status_code == 403
        worker = Worker.objects.get(hostname='victim-host')
        resp = client.post(action_url(worker, 'regenerate_token'))
        assert resp.status_code == 403

    def test_re_enroll_reuses_suffixed_user(self, seeded_enrollment_key):
        _make_operator('staff')
        first = self._enroll(seeded_enrollment_key)
        second = self._enroll(seeded_enrollment_key)

        assert second.user_id == first.user_id
        assert User.objects.filter(
            username__startswith='worker_victim-host',
        ).count() == 2

    def test_auto_enroll_uses_suffixed_user(self):
        _make_operator('staff')

        envelope = auto_enroll_local_worker(hostname='victim-host')

        token = Token.objects.get(key=envelope['api_token'])
        _assert_plain_worker_user(token.user)

    def test_user_owned_by_other_worker_is_skipped(self, seeded_enrollment_key):
        """``worker_victim-host`` already backing another host is not shared."""
        owner = User.objects.create_user(username='worker_victim-host')
        Worker.objects.create(hostname='someone-else', user=owner)

        token = self._enroll(seeded_enrollment_key)

        _assert_plain_worker_user(token.user)
        assert Worker.objects.get(hostname='someone-else').user == owner
