# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for ``GET /api/heartbeat/`` derived fields.

Split from ``test_worker_detail_fields.py`` to keep each file under
the 300-line Python cap (CLAUDE.md).  This file owns the listing /
aliasing / staleness / migration-default checks; the sibling file owns
the POST-side field-persistence checks.
"""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from workers.constants import WORKER_STALENESS_SECONDS
from workers.models import Worker

User = get_user_model()

HEARTBEAT_URL = '/api/heartbeat/'


def _full_heartbeat(client, hostname, extra=None):
    """Send a full-registration heartbeat (with ``os`` set)."""
    data = {
        'hostname': hostname,
        'os': 'Linux',
        'ip_address': '10.0.0.1',
        'available_tools': {},
    }
    if extra:
        data.update(extra)
    return client.post(HEARTBEAT_URL, data=data, format='json')


@pytest.mark.django_db
class TestStalenessDerivedOffline:
    """Verify stale workers are reported as OFFLINE in the API."""

    def test_stale_worker_shows_offline(
        self, make_worker_client, admin_client, default_version,
    ):
        _, client, _ = make_worker_client('stale-worker')
        _full_heartbeat(client, 'stale-worker', {'status': 'IDLE'})
        worker = Worker.objects.get(hostname='stale-worker')
        stale_time = (
            timezone.now()
            - timedelta(seconds=WORKER_STALENESS_SECONDS + 30)
        )
        Worker.objects.filter(pk=worker.pk).update(last_seen=stale_time)

        resp = admin_client.get(HEARTBEAT_URL)
        assert resp.status_code == 200
        match = [
            w for w in resp.data if w['hostname'] == 'stale-worker'
        ]
        assert len(match) == 1
        assert match[0]['status'] == 'OFFLINE'

    def test_fresh_worker_shows_stored_status(
        self, make_worker_client, admin_client, default_version,
    ):
        _, client, _ = make_worker_client('fresh-worker')
        _full_heartbeat(client, 'fresh-worker', {'status': 'RENDERING'})
        resp = admin_client.get(HEARTBEAT_URL)
        assert resp.status_code == 200
        match = [
            w for w in resp.data if w['hostname'] == 'fresh-worker'
        ]
        assert len(match) == 1
        assert match[0]['status'] == 'RENDERING'


@pytest.mark.django_db
class TestLastHeartbeatAlias:
    """Verify last_heartbeat alias matches last_seen."""

    def test_last_heartbeat_equals_last_seen(
        self, make_worker_client, admin_client, default_version,
    ):
        _, client, _ = make_worker_client('alias-worker')
        _full_heartbeat(client, 'alias-worker')
        resp = admin_client.get(HEARTBEAT_URL)
        assert resp.status_code == 200
        match = [
            w for w in resp.data if w['hostname'] == 'alias-worker'
        ]
        assert len(match) == 1
        assert match[0]['last_heartbeat'] == match[0]['last_seen']


@pytest.mark.django_db
class TestMigrationDefaults:
    """Verify Worker records get sensible defaults."""

    def test_worker_created_without_new_fields_has_defaults(self):
        user = User.objects.create_user(username='bare_worker_user')
        worker = Worker.objects.create(
            hostname='bare-worker', user=user,
        )
        worker.refresh_from_db()
        assert worker.cpu_name == ''
        assert worker.gpu_name == ''
        assert worker.status == 'OFFLINE'
