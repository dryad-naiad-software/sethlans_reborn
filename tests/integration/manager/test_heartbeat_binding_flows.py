# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for issue #215 (continued): non-worker callers are
rejected, and the legitimate flows keep working.

* Admin/dashboard accounts (session login, force-authenticated, or an
  admin's own DRF token) cannot heartbeat or register a worker.
* A worker's own heartbeat and re-registration, the enroll -> register
  -> heartbeat flow, the in-process auto-enroll flow, and the dashboard
  ``list`` all still work.  The response never carries an API token.
"""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from workers.models import Worker
from workers.services.auto_enroll import auto_enroll_local_worker

from .conftest import TEST_ENROLLMENT_KEY_CANONICAL, fresh_nonce
from .heartbeat_binding_helpers import (
    ENROLL_URL,
    FULL_REGISTRATION,
    HEARTBEAT_URL,
    make_two_workers,
    snapshot,
)

User = get_user_model()


@pytest.fixture
def two_workers(db, default_version):
    """Two enrolled workers, A (``host-a``) and B (``host-b``)."""
    return make_two_workers()


@pytest.mark.django_db
class TestNonWorkerCallersForbidden:
    """Admin/dashboard accounts cannot heartbeat or register."""

    @pytest.mark.parametrize('extra', [{}, FULL_REGISTRATION])
    def test_session_admin_cannot_heartbeat_existing_worker(
        self, admin_user, two_workers, extra,
    ):
        _, (worker_b, token_b, _) = two_workers
        before = snapshot(worker_b)
        client = APIClient()
        client.force_login(admin_user)

        resp = client.post(
            HEARTBEAT_URL,
            data={'hostname': 'host-b', **extra},
            format='json',
        )

        assert resp.status_code == 403
        assert token_b.key not in resp.content.decode()
        assert snapshot(worker_b) == before

    def test_session_admin_cannot_register_new_hostname(
        self, admin_user, default_version,
    ):
        client = APIClient()
        client.force_login(admin_user)
        user_count = User.objects.count()

        resp = client.post(
            HEARTBEAT_URL,
            data={'hostname': 'admin-made', **FULL_REGISTRATION},
            format='json',
        )

        assert resp.status_code == 403
        assert not Worker.objects.filter(hostname='admin-made').exists()
        assert User.objects.count() == user_count
        assert not Token.objects.filter(user=admin_user).exists()

    def test_admin_token_cannot_register(self, admin_user, default_version):
        """A token belonging to a non-worker user is rejected too."""
        token = Token.objects.create(user=admin_user)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')

        resp = client.post(
            HEARTBEAT_URL,
            data={'hostname': 'admin-token-host', **FULL_REGISTRATION},
            format='json',
        )

        assert resp.status_code == 403
        assert not Worker.objects.filter(hostname='admin-token-host').exists()

    def test_force_authenticated_admin_cannot_heartbeat(
        self, admin_client, two_workers,
    ):
        resp = admin_client.post(
            HEARTBEAT_URL, data={'hostname': 'host-a'}, format='json',
        )
        assert resp.status_code == 403


@pytest.mark.django_db
class TestLegitimateFlowsStillWork:
    """Own-worker heartbeats, enrollment, and the dashboard list."""

    def test_own_heartbeat_and_reregistration_succeed(self, two_workers):
        (worker_a, _, client_a), _ = two_workers

        hb = client_a.post(
            HEARTBEAT_URL,
            data={'hostname': 'host-a', 'status': 'RENDERING'},
            format='json',
        )
        assert hb.status_code == 200
        assert hb.data['id'] == worker_a.pk

        reg = client_a.post(
            HEARTBEAT_URL,
            data={'hostname': 'host-a', **FULL_REGISTRATION},
            format='json',
        )
        assert reg.status_code == 200
        assert 'token' not in reg.data
        worker_a.refresh_from_db()
        assert worker_a.os == 'Windows'
        assert worker_a.available_tools == {'blender': ['9.9.9']}
        assert Worker.objects.filter(user=worker_a.user).count() == 1

    def test_registration_sanitises_self_reported_fields(self, two_workers):
        """Invalid ip/os/tools are coerced, not stored verbatim or 500ed."""
        (worker_a, _, client_a), _ = two_workers

        resp = client_a.post(
            HEARTBEAT_URL,
            data={
                'hostname': 'host-a',
                'os': 'L' * 500,
                'ip_address': 'not-an-ip',
                'available_tools': ['not', 'a', 'dict'],
            },
            format='json',
        )

        assert resp.status_code == 200
        worker_a.refresh_from_db()
        assert worker_a.os == 'L' * 100
        assert worker_a.ip_address is None
        assert worker_a.available_tools == {}

    def test_heartbeat_ignores_oversized_or_non_dict_tools(self, two_workers):
        (worker_a, _, client_a), _ = two_workers

        for tools in ('blender', {'blender': ['x' * (70 * 1024)]}):
            resp = client_a.post(
                HEARTBEAT_URL,
                data={'hostname': 'host-a', 'available_tools': tools},
                format='json',
            )
            assert resp.status_code == 200
            worker_a.refresh_from_db()
            assert worker_a.available_tools == {}

    def test_enroll_then_register_then_heartbeat(
        self,
        seeded_enrollment_key,
        ready_runtime_state,
        reset_enroll_module_state,
        default_version,
    ):
        enroll = APIClient().post(
            ENROLL_URL,
            {
                'enrollment_key': TEST_ENROLLMENT_KEY_CANONICAL,
                'hostname': 'flow-host',
                'nonce': fresh_nonce(),
            },
            format='json',
        )
        assert enroll.status_code == 200
        api_token = enroll.json()['payload']['api_token']
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {api_token}')

        reg = client.post(
            HEARTBEAT_URL,
            data={'hostname': 'flow-host', 'os': 'Linux',
                  'ip_address': '10.0.0.5',
                  'available_tools': {'blender': ['4.2.19']}},
            format='json',
        )
        assert reg.status_code == 200
        assert 'token' not in reg.data
        assert reg.data['required_blender_versions']

        hb = client.post(
            HEARTBEAT_URL, data={'hostname': 'flow-host'}, format='json',
        )
        assert hb.status_code == 200
        assert hb.data['id'] == reg.data['id']

        worker = Worker.objects.get(hostname='flow-host')
        assert worker.os == 'Linux'
        assert Token.objects.get(user=worker.user).key == api_token
        assert Worker.objects.filter(hostname='flow-host').count() == 1

    def test_auto_enrolled_local_worker_can_register(
        self, ready_runtime_state, default_version,
    ):
        envelope = auto_enroll_local_worker(hostname='local-host')
        client = APIClient()
        client.credentials(
            HTTP_AUTHORIZATION=f"Token {envelope['api_token']}",
        )

        resp = client.post(
            HEARTBEAT_URL,
            data={'hostname': 'local-host', 'os': 'Linux',
                  'available_tools': {}},
            format='json',
        )

        assert resp.status_code == 200
        assert Worker.objects.get(hostname='local-host').os == 'Linux'

    def test_dashboard_list_still_works_for_admin(
        self, admin_client, two_workers,
    ):
        resp = admin_client.get(HEARTBEAT_URL)

        assert resp.status_code == 200
        hostnames = {w['hostname'] for w in resp.data}
        assert {'host-a', 'host-b'} <= hostnames
