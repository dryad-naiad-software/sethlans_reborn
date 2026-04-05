# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Integration tests for worker detail fields (issue #30)."""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from workers.constants import WORKER_STALENESS_SECONDS
from workers.models import Worker

User = get_user_model()

HEARTBEAT_URL = '/api/heartbeat/'


@pytest.fixture(autouse=True)
def _set_enrollment_key(monkeypatch):
    monkeypatch.setenv('SETHLANS_SECURITY_ENROLLMENT_KEY', 'test-key-123')


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    from workers.views.heartbeat import _enrollment_rate_limiter
    with _enrollment_rate_limiter._lock:
        _enrollment_rate_limiter._attempts.clear()


def _enroll_worker(hostname, extra_data=None):
    """Enroll a worker via the API, returning (response, APIClient)."""
    client = APIClient()
    data = {'hostname': hostname, 'os': 'Linux',
            'ip_address': '10.0.0.1', 'available_tools': {}}
    if extra_data:
        data.update(extra_data)
    resp = client.post(HEARTBEAT_URL, data=data, format='json',
                       HTTP_X_ENROLLMENT_KEY='test-key-123')
    if resp.status_code == 200 and 'token' in resp.data:
        client.credentials(
            HTTP_AUTHORIZATION=f'Token {resp.data["token"]}')
    return resp, client


@pytest.mark.django_db
class TestHeartbeatPostNewFields:
    """Verify heartbeat POST persists cpu_name, status, gpu_name."""

    def test_enrollment_stores_cpu_name(self, default_version):
        resp, _ = _enroll_worker('cpu-worker', {
            'cpu_name': 'Intel Core i7-12700H',
        })
        assert resp.status_code == 200
        worker = Worker.objects.get(hostname='cpu-worker')
        assert worker.cpu_name == 'Intel Core i7-12700H'

    def test_enrollment_stores_status_idle(self, default_version):
        resp, _ = _enroll_worker('idle-worker', {'status': 'IDLE'})
        assert resp.status_code == 200
        worker = Worker.objects.get(hostname='idle-worker')
        assert worker.status == 'IDLE'

    def test_enrollment_stores_status_rendering(self, default_version):
        resp, _ = _enroll_worker('render-worker', {
            'status': 'RENDERING',
        })
        assert resp.status_code == 200
        worker = Worker.objects.get(hostname='render-worker')
        assert worker.status == 'RENDERING'

    def test_enrollment_stores_gpu_name_from_tools(
        self, default_version,
    ):
        tools = {
            'blender': ['4.2.19'],
            'gpu_devices_details': [
                {'name': 'NVIDIA RTX 4090', 'type': 'CUDA'},
            ],
        }
        resp, _ = _enroll_worker('gpu-worker', {
            'available_tools': tools,
        })
        assert resp.status_code == 200
        worker = Worker.objects.get(hostname='gpu-worker')
        assert worker.gpu_name == 'NVIDIA RTX 4090'

    def test_enrollment_stores_multi_gpu_names(self, default_version):
        tools = {
            'gpu_devices_details': [
                {'name': 'NVIDIA RTX 3090', 'type': 'CUDA'},
                {'name': 'NVIDIA RTX 4090', 'type': 'CUDA'},
            ],
        }
        resp, _ = _enroll_worker('multi-gpu', {
            'available_tools': tools,
        })
        assert resp.status_code == 200
        worker = Worker.objects.get(hostname='multi-gpu')
        assert worker.gpu_name == 'NVIDIA RTX 3090, NVIDIA RTX 4090'

    def test_heartbeat_update_persists_new_fields(
        self, default_version,
    ):
        """Token heartbeat updates cpu_name, status, gpu_name."""
        resp, client = _enroll_worker('update-worker', {
            'cpu_name': 'AMD Ryzen 9',
            'status': 'IDLE',
        })
        assert resp.status_code == 200

        tools = {
            'gpu_devices_details': [
                {'name': 'AMD Radeon RX 7900'},
            ],
        }
        resp2 = client.post(
            HEARTBEAT_URL,
            data={
                'hostname': 'update-worker',
                'cpu_name': 'AMD Ryzen 9 7950X',
                'status': 'RENDERING',
                'available_tools': tools,
            },
            format='json',
        )
        assert resp2.status_code == 200
        worker = Worker.objects.get(hostname='update-worker')
        assert worker.cpu_name == 'AMD Ryzen 9 7950X'
        assert worker.status == 'RENDERING'
        assert worker.gpu_name == 'AMD Radeon RX 7900'


@pytest.mark.django_db
class TestHeartbeatGetNewFields:
    """Verify GET /api/heartbeat/ returns the new fields."""

    def test_list_includes_new_fields(
        self, admin_client, default_version,
    ):
        _enroll_worker('list-worker', {
            'cpu_name': 'Apple M1 Pro',
            'status': 'IDLE',
            'available_tools': {
                'gpu_devices_details': [
                    {'name': 'Apple M1 Pro GPU'},
                ],
            },
        })
        resp = admin_client.get(HEARTBEAT_URL)
        assert resp.status_code == 200
        workers = resp.data
        match = [w for w in workers if w['hostname'] == 'list-worker']
        assert len(match) == 1
        w = match[0]
        assert w['cpu_name'] == 'Apple M1 Pro'
        assert w['gpu_name'] == 'Apple M1 Pro GPU'
        assert w['status'] in ('IDLE', 'RENDERING', 'OFFLINE')
        assert 'last_seen' in w
        assert 'last_heartbeat' in w


@pytest.mark.django_db
class TestStatusValidationViaAPI:
    """Verify invalid status values are defaulted to IDLE."""

    def test_bogus_status_defaults_to_idle(self, default_version):
        resp, _ = _enroll_worker('bogus-worker', {
            'status': 'BOGUS',
        })
        assert resp.status_code == 200
        worker = Worker.objects.get(hostname='bogus-worker')
        assert worker.status == 'IDLE'

    def test_offline_status_rejected_from_worker(
        self, default_version,
    ):
        """Workers cannot set OFFLINE -- only manager derives it."""
        resp, _ = _enroll_worker('offline-attempt', {
            'status': 'OFFLINE',
        })
        assert resp.status_code == 200
        worker = Worker.objects.get(hostname='offline-attempt')
        assert worker.status == 'IDLE'

    def test_rendering_status_accepted(self, default_version):
        resp, _ = _enroll_worker('rendering-worker', {
            'status': 'RENDERING',
        })
        assert resp.status_code == 200
        worker = Worker.objects.get(hostname='rendering-worker')
        assert worker.status == 'RENDERING'

    def test_missing_status_defaults_to_idle(self, default_version):
        resp, _ = _enroll_worker('no-status-worker')
        assert resp.status_code == 200
        worker = Worker.objects.get(hostname='no-status-worker')
        assert worker.status == 'IDLE'


@pytest.mark.django_db
class TestCpuNameSanitizationViaAPI:
    """Verify cpu_name with HTML/script chars is rejected."""

    def test_script_tag_sanitized_to_empty(self, default_version):
        resp, _ = _enroll_worker('xss-worker', {
            'cpu_name': '<script>alert(1)</script>',
        })
        assert resp.status_code == 200
        worker = Worker.objects.get(hostname='xss-worker')
        assert worker.cpu_name == ''

    def test_valid_cpu_name_preserved(self, default_version):
        resp, _ = _enroll_worker('good-cpu', {
            'cpu_name': 'Intel(R) Core(TM) i9-13900K @ 5.80GHz',
        })
        assert resp.status_code == 200
        worker = Worker.objects.get(hostname='good-cpu')
        assert worker.cpu_name == 'Intel(R) Core(TM) i9-13900K @ 5.80GHz'

    def test_windows_registry_format_preserved(self, default_version):
        name = 'Intel64 Family 6 Model 154 Stepping 3, GenuineIntel'
        resp, _ = _enroll_worker('win-cpu', {'cpu_name': name})
        assert resp.status_code == 200
        worker = Worker.objects.get(hostname='win-cpu')
        assert worker.cpu_name == name


@pytest.mark.django_db
class TestStalenessDerivedOffline:
    """Verify stale workers are reported as OFFLINE in the API."""

    def test_stale_worker_shows_offline(
        self, admin_client, default_version,
    ):
        _enroll_worker('stale-worker', {'status': 'IDLE'})
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
        self, admin_client, default_version,
    ):
        _enroll_worker('fresh-worker', {'status': 'RENDERING'})
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
        self, admin_client, default_version,
    ):
        _enroll_worker('alias-worker')
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
