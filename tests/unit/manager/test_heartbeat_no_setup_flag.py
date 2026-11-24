# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Regression tests for issue #304: the heartbeat response carries no
``manager_setup_complete`` field.

The field told workers to idle until the manager's setup marker said
setup was done.  The launcher now starts the manager only after setup,
so the gate was removed; a marker looked up in the wrong folder must
no longer be able to idle the farm.  Both branches (plain heartbeat
and full registration) are checked, with no marker and with an
unfinished marker in the manager data folder.
"""

from __future__ import annotations

import json

import pytest
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from workers.models import Worker
from workers.views import heartbeat as heartbeat_mod

User = get_user_model()

HEARTBEAT_URL = '/api/heartbeat/'

pytestmark = pytest.mark.django_db


def _client_for(username, hostname, **worker_fields):
    user = User.objects.create_user(username=username)
    user.set_unusable_password()
    user.save()
    Worker.objects.create(
        hostname=hostname, user=user, is_active=True, **worker_fields,
    )
    token = Token.objects.create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return client


@pytest.fixture
def worker_client():
    """An enrolled, registered worker (plain-heartbeat branch)."""
    return 'noflag', _client_for(
        'worker_noflag', 'noflag', available_tools={'blender': ['4.2.19']},
    )


@pytest.fixture
def fresh_client():
    """An enrolled worker that has not sent its full registration."""
    return 'noflagfresh', _client_for('worker_noflagfresh', 'noflagfresh')


@pytest.fixture
def unfinished_marker_dir(monkeypatch, tmp_path):
    """Point the manager data folder at ``tmp_path`` holding an
    unfinished setup marker (``completed_at: null``), the state that
    used to report ``manager_setup_complete: False``."""
    (tmp_path / '.setup_complete').write_text(json.dumps({
        'version': 1, 'completed_at': None,
        'topology': 'manager', 'checkpoints': ['topology_chosen'],
    }), encoding='utf-8')
    monkeypatch.setenv('SETHLANS_MANAGER_DATA_DIR', str(tmp_path))
    return tmp_path


@pytest.fixture
def empty_data_dir(monkeypatch, tmp_path):
    """Point the manager data folder at an empty ``tmp_path``."""
    monkeypatch.setenv('SETHLANS_MANAGER_DATA_DIR', str(tmp_path))
    return tmp_path


class TestPlainHeartbeat:

    def test_no_flag_without_a_marker(self, worker_client, empty_data_dir):
        hostname, client = worker_client
        resp = client.post(
            HEARTBEAT_URL, data={'hostname': hostname}, format='json',
        )
        assert resp.status_code == 200
        assert 'manager_setup_complete' not in resp.data

    def test_no_flag_with_an_unfinished_marker(
        self, worker_client, unfinished_marker_dir,
    ):
        hostname, client = worker_client
        resp = client.post(
            HEARTBEAT_URL, data={'hostname': hostname}, format='json',
        )
        assert resp.status_code == 200
        assert 'manager_setup_complete' not in resp.data
        # The rest of the envelope is still sent.
        assert 'required_blender_versions' in resp.data


class TestFullRegistration:

    def test_no_flag_on_full_registration(
        self, fresh_client, unfinished_marker_dir,
    ):
        hostname, client = fresh_client
        resp = client.post(
            HEARTBEAT_URL,
            data={
                'hostname': hostname,
                'os': 'Linux',
                'available_tools': {'blender': ['4.2.19']},
            },
            format='json',
        )
        assert resp.status_code == 200
        assert Worker.objects.get(hostname=hostname).os == 'Linux'
        assert 'manager_setup_complete' not in resp.data


class TestNoMarkerLookup:

    @pytest.mark.parametrize('name', [
        '_get_data_dir',
        'is_setup_complete',
        '_is_setup_complete_cached',
        '_reset_setup_complete_cache',
        '_setup_complete_cached',
    ])
    def test_heartbeat_module_has_no_setup_marker_helpers(self, name):
        """The view no longer reads the setup marker at all."""
        assert not hasattr(heartbeat_mod, name)
