# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the ``manager_setup_complete`` field on the heartbeat
response (issue #126).

The field is injected in
``WorkerHeartbeatViewSet._process_heartbeat`` so it MUST appear on
both the plain-heartbeat response and the full-registration response,
and its value MUST reflect the on-disk sentinel state read fresh on
every call (no caching).

These tests deliberately do NOT mock ``is_setup_complete``: they write
real sentinel files via ``write_sentinel`` / ``create_sentinel`` so the
real read path is exercised and any change to either side of the
contract surfaces here.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from workers.models import Worker
from workers.services.sentinel import create_sentinel, write_sentinel
from workers.views import heartbeat as heartbeat_mod

User = get_user_model()

HEARTBEAT_URL = '/api/heartbeat/'

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Local fixtures (the unit conftest is mock-only; we need a real Worker+token
# because heartbeat is a token-authenticated DB-backed view)
# ---------------------------------------------------------------------------


@pytest.fixture
def worker_with_token():
    """Create a Worker, linked User, and APIClient with token credentials."""
    user = User.objects.create_user(username='worker_unitsetup')
    user.set_unusable_password()
    user.save()
    worker = Worker.objects.create(
        hostname='unitsetup',
        user=user,
        is_active=True,
        available_tools={'blender': ['4.2.19']},
    )
    token = Token.objects.create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return worker, client


@pytest.fixture
def fresh_worker_client():
    """Return (hostname, client) for a token-authed worker that has NOT
    yet been registered (no Worker row).  Used to drive the
    full-registration branch — the first heartbeat from a freshly
    enrolled worker carries an ``os`` field and creates the Worker row.
    """
    user = User.objects.create_user(username='worker_freshreg')
    user.set_unusable_password()
    user.save()
    token = Token.objects.create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return 'freshreg', client


@pytest.fixture
def patch_data_dir(mocker, tmp_path):
    """Pin heartbeat's ``_get_data_dir`` to ``tmp_path``.

    This is the same patch target used by ``test_status_public_view``
    — heartbeat imports ``_get_data_dir`` by name, so the local binding
    on ``workers.views.heartbeat`` is what we must override.
    """
    mocker.patch.object(
        heartbeat_mod, '_get_data_dir', return_value=tmp_path,
    )
    return tmp_path


# ---------------------------------------------------------------------------
# Plain-heartbeat branch (no ``os`` field in payload)
# ---------------------------------------------------------------------------


class TestManagerSetupCompleteOnHeartbeat:
    """``manager_setup_complete`` on the plain-heartbeat response."""

    def test_true_when_sentinel_has_completed_at(
        self, worker_with_token, patch_data_dir,
    ):
        """A finalized sentinel (``completed_at`` set) yields True."""
        worker, client = worker_with_token
        # create_sentinel writes a sentinel with a fresh ISO timestamp
        # in ``completed_at`` — the canonical "setup done" state.
        create_sentinel(
            patch_data_dir, 'manager', ['topology_chosen', 'verified'],
        )

        resp = client.post(
            HEARTBEAT_URL,
            data={'hostname': worker.hostname},
            format='json',
        )

        assert resp.status_code == 200
        assert 'manager_setup_complete' in resp.data
        assert resp.data['manager_setup_complete'] is True

    def test_false_when_sentinel_is_mid_wizard(
        self, worker_with_token, patch_data_dir,
    ):
        """A checkpoint sentinel (``completed_at: None``) yields False.

        ``append_checkpoint`` writes this shape while the wizard is in
        progress; ``is_setup_complete`` deliberately reports False for
        it so the SetupGateMiddleware contract is preserved.
        """
        worker, client = worker_with_token
        write_sentinel(patch_data_dir, {
            'version': 1,
            'completed_at': None,
            'topology': 'manager',
            'checkpoints': ['topology_chosen'],
        })

        resp = client.post(
            HEARTBEAT_URL,
            data={'hostname': worker.hostname},
            format='json',
        )

        assert resp.status_code == 200
        assert 'manager_setup_complete' in resp.data
        assert resp.data['manager_setup_complete'] is False

    def test_false_when_sentinel_file_missing(
        self, worker_with_token, patch_data_dir,
    ):
        """No sentinel file at all yields False.

        ``patch_data_dir`` (which is ``tmp_path``) exists but contains
        nothing — ``read_sentinel`` returns None, so
        ``is_setup_complete`` returns False.
        """
        worker, client = worker_with_token
        # Sanity: ensure the directory really has no sentinel file.
        assert not (patch_data_dir / '.setup_complete').exists()

        resp = client.post(
            HEARTBEAT_URL,
            data={'hostname': worker.hostname},
            format='json',
        )

        assert resp.status_code == 200
        assert 'manager_setup_complete' in resp.data
        assert resp.data['manager_setup_complete'] is False


# ---------------------------------------------------------------------------
# Full-registration branch (payload carries an ``os`` field)
# ---------------------------------------------------------------------------


class TestManagerSetupCompleteOnFullRegistration:
    """``manager_setup_complete`` on the full-registration response.

    The injection happens in ``_process_heartbeat`` (after the handler
    dispatch) so it MUST also appear on the very first heartbeat from
    a newly enrolled worker — the one that creates the ``Worker`` row.
    Lock that in here so a future refactor that pushes the field down
    into ``_handle_heartbeat`` only would be caught immediately.
    """

    def test_field_present_on_full_registration_response(
        self, fresh_worker_client, patch_data_dir,
    ):
        hostname, client = fresh_worker_client
        # Sentinel finalized — registration response should report True.
        create_sentinel(patch_data_dir, 'manager', ['verified'])

        resp = client.post(
            HEARTBEAT_URL,
            data={
                'hostname': hostname,
                'os': 'Linux',
                'ip_address': '192.168.1.50',
                'available_tools': {'blender': ['4.2.19']},
            },
            format='json',
        )

        assert resp.status_code == 200
        # Sanity: the full-registration branch ran (Worker row was created
        # and a token field is present in the response envelope).
        assert Worker.objects.filter(hostname=hostname).exists()
        assert 'token' in resp.data
        # The field under test:
        assert 'manager_setup_complete' in resp.data
        assert resp.data['manager_setup_complete'] is True

    def test_field_false_on_full_registration_when_sentinel_missing(
        self, fresh_worker_client, patch_data_dir,
    ):
        """Same as above but with no sentinel — field must still appear,
        and its value must be False.  Catches any accidental gating on
        the registration branch (e.g. only injecting when truthy).
        """
        hostname, client = fresh_worker_client
        assert not (patch_data_dir / '.setup_complete').exists()

        resp = client.post(
            HEARTBEAT_URL,
            data={
                'hostname': hostname,
                'os': 'Linux',
                'available_tools': {},
            },
            format='json',
        )

        assert resp.status_code == 200
        assert 'manager_setup_complete' in resp.data
        assert resp.data['manager_setup_complete'] is False
