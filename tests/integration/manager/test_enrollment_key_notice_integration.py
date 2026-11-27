# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The dashboard's one-time enrollment key notice through the real API (setup-rebuild FR-WZ-9).

The callers log in the way the dashboard does (CSRF cookie, session login,
``X-CSRFToken`` on the POST) or use a worker's API token; the notice view,
the permission class and the ``ManagerSettings`` row (migration 0036) are real.
"""
import pytest
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from workers import enrollment_key
from workers.models import ManagerSettings

from .conftest import TEST_ENROLLMENT_KEY_CANONICAL, enroll_body, reply_token

NOTICE_URL = '/api/auth/enrollment_key_notice/'
REGENERATE_URL = '/api/auth/regenerate_enrollment_key/'
ENROLL_URL = '/api/enroll/'
PASSWORD = 'Notice-Test-Password-41c7'
HIDDEN = {'show': False, 'enrollment_key': None}


def _known_key():
    """Pin the singleton row (an earlier transactional test's flush may have removed the seeded one)."""
    ManagerSettings.objects.update_or_create(
        pk=1, defaults={'manager_id': '00000000-0000-0000-0000-0000000000a1',
                        'enrollment_key': TEST_ENROLLMENT_KEY_CANONICAL})


def _logged_in(user) -> APIClient:
    """A session client as the dashboard makes it: CSRF cookie, login, then the token header."""
    client = APIClient(enforce_csrf_checks=True)
    assert client.get('/api/auth/csrf/').status_code == 200
    resp = client.post('/api/auth/login/', {'username': user.username, 'password': PASSWORD}, format='json',
                       HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value)
    assert resp.status_code == 200, resp.content
    client.credentials(HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value)  # login rotates the token
    return client


@pytest.mark.django_db
class TestEnrollmentKeyNoticeIntegration:
    def test_admin_sees_the_key_once_then_dismisses_it(self):
        """FR-WZ-9: shown with the key until an admin dismisses it; hidden for good, also after a rotation."""
        _known_key()
        admin = get_user_model().objects.create_superuser('notice-admin', 'admin@example.com', PASSWORD)
        client = _logged_in(admin)

        shown = client.get(NOTICE_URL)
        assert shown.status_code == 200
        assert shown.json() == {'show': True,
                                'enrollment_key': enrollment_key.format_display(TEST_ENROLLMENT_KEY_CANONICAL)}
        assert shown['Cache-Control'] == 'no-store'

        dismissed = client.post(NOTICE_URL)
        assert dismissed.status_code == 200
        assert dismissed.json() == HIDDEN
        assert dismissed['Cache-Control'] == 'no-store'
        assert ManagerSettings.objects.get(pk=1).enrollment_key_notice_dismissed is True
        assert client.get(NOTICE_URL).json() == HIDDEN

        assert client.post(REGENERATE_URL).status_code == 200
        assert client.get(NOTICE_URL).json() == HIDDEN

    @pytest.mark.parametrize('caller', ['anonymous', 'user', 'worker', 'staff_worker'])
    def test_non_admin_is_refused(self, caller, request):
        """FR-WZ-9: an anonymous caller, a logged-in non-admin, a worker token and a worker account made
        staff get 403 (session authentication comes first, so DRF answers 403, not 401); none can see the
        key, dismiss the notice or rotate the key (``IsAdminNotWorker``)."""
        _known_key()
        client = _caller(caller, request)

        for method, url in ((client.get, NOTICE_URL), (client.post, NOTICE_URL), (client.post, REGENERATE_URL)):
            resp = method(url)
            assert resp.status_code == 403, (url, resp.content)
            body = resp.content.decode()
            assert 'enrollment_key' not in resp.json()
            assert enrollment_key.format_display(TEST_ENROLLMENT_KEY_CANONICAL) not in body
            assert TEST_ENROLLMENT_KEY_CANONICAL not in body
        row = ManagerSettings.objects.get(pk=1)
        assert row.enrollment_key_notice_dismissed is False
        assert row.enrollment_key == TEST_ENROLLMENT_KEY_CANONICAL


def _caller(kind: str, request) -> APIClient:
    """The client of one refused caller (``test_non_admin_is_refused``)."""
    if kind == 'anonymous':
        return APIClient(enforce_csrf_checks=True)
    if kind == 'user':
        return _logged_in(get_user_model().objects.create_user('notice-user', 'user@example.com', PASSWORD))
    if kind == 'worker':
        _worker, client, _token = request.getfixturevalue('make_worker_client')('notice-worker')
        return client
    return _staff_worker(request)


def _staff_worker(request) -> APIClient:
    """A worker enrolled through the real ``/api/enroll/``, whose account is then made staff.

    Enrollment never makes a staff account (``get_or_create_worker_user``); the flag is set the way
    Django admin's "Staff status" box sets it, the case ``IsAdminNotWorker`` exists for.
    """
    request.getfixturevalue('ready_runtime_state')
    request.getfixturevalue('reset_enroll_module_state')
    enrolled = APIClient().post(ENROLL_URL, enroll_body(hostname='notice-staff-worker'), format='json')
    assert enrolled.status_code == 200, enrolled.content
    token = reply_token(enrolled.json())
    user = Token.objects.get(key=token).user
    assert hasattr(user, 'worker_profile') and not user.is_staff
    user.is_staff = True
    user.save(update_fields=['is_staff'])
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token}')
    return client
