# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for worker detail fields (issue #30).

All tests exercise ``POST /api/heartbeat/`` as a token-authenticated
request — the old ``X-Enrollment-Key`` pseudo-enrollment path has been
removed per spec FR-19.  Tests that used to rely on that helper now use
the ``make_worker_client`` factory (from ``conftest.py``) which creates
an in-DB ``Worker`` + ``User`` + ``Token`` triple and returns a
pre-credentialed ``APIClient``.  The first heartbeat each test sends is
a full-registration payload (with ``os``), so the fields being tested
(``cpu_name``, ``status``, ``gpu_name``) travel the exact code path the
worker agent uses after enrollment.
"""

import pytest

from workers.models import Worker

HEARTBEAT_URL = '/api/heartbeat/'


def _full_heartbeat(client, hostname, extra=None):
    """Send a full-registration heartbeat (with ``os`` set).

    Mirrors the first heartbeat a real worker sends after enrollment —
    ``os`` being present routes the request to ``_handle_full_registration``
    which is where the fields under test get populated.
    """
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
class TestHeartbeatPostNewFields:
    """Verify heartbeat POST persists cpu_name, status, gpu_name."""

    def test_enrollment_stores_cpu_name(
        self, make_worker_client, default_version,
    ):
        _, client, _ = make_worker_client('cpu-worker')
        resp = _full_heartbeat(client, 'cpu-worker', {
            'cpu_name': 'Intel Core i7-12700H',
        })
        assert resp.status_code == 200
        worker = Worker.objects.get(hostname='cpu-worker')
        assert worker.cpu_name == 'Intel Core i7-12700H'

    def test_enrollment_stores_status_idle(
        self, make_worker_client, default_version,
    ):
        _, client, _ = make_worker_client('idle-worker')
        resp = _full_heartbeat(client, 'idle-worker', {'status': 'IDLE'})
        assert resp.status_code == 200
        worker = Worker.objects.get(hostname='idle-worker')
        assert worker.status == 'IDLE'

    def test_enrollment_stores_status_rendering(
        self, make_worker_client, default_version,
    ):
        _, client, _ = make_worker_client('render-worker')
        resp = _full_heartbeat(client, 'render-worker', {
            'status': 'RENDERING',
        })
        assert resp.status_code == 200
        worker = Worker.objects.get(hostname='render-worker')
        assert worker.status == 'RENDERING'

    def test_enrollment_stores_gpu_name_from_tools(
        self, make_worker_client, default_version,
    ):
        _, client, _ = make_worker_client('gpu-worker')
        tools = {
            'blender': ['4.2.19'],
            'gpu_devices_details': [
                {'name': 'NVIDIA RTX 4090', 'type': 'CUDA'},
            ],
        }
        resp = _full_heartbeat(client, 'gpu-worker', {
            'available_tools': tools,
        })
        assert resp.status_code == 200
        worker = Worker.objects.get(hostname='gpu-worker')
        assert worker.gpu_name == 'NVIDIA RTX 4090'

    def test_enrollment_stores_multi_gpu_names(
        self, make_worker_client, default_version,
    ):
        _, client, _ = make_worker_client('multi-gpu')
        tools = {
            'gpu_devices_details': [
                {'name': 'NVIDIA RTX 3090', 'type': 'CUDA'},
                {'name': 'NVIDIA RTX 4090', 'type': 'CUDA'},
            ],
        }
        resp = _full_heartbeat(client, 'multi-gpu', {
            'available_tools': tools,
        })
        assert resp.status_code == 200
        worker = Worker.objects.get(hostname='multi-gpu')
        assert worker.gpu_name == 'NVIDIA RTX 3090, NVIDIA RTX 4090'

    def test_heartbeat_update_persists_new_fields(
        self, make_worker_client, default_version,
    ):
        """Token heartbeat updates cpu_name, status, gpu_name."""
        _, client, _ = make_worker_client('update-worker')
        resp = _full_heartbeat(client, 'update-worker', {
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
        self, make_worker_client, admin_client, default_version,
    ):
        _, client, _ = make_worker_client('list-worker')
        _full_heartbeat(client, 'list-worker', {
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

    def test_bogus_status_defaults_to_idle(
        self, make_worker_client, default_version,
    ):
        _, client, _ = make_worker_client('bogus-worker')
        resp = _full_heartbeat(client, 'bogus-worker', {
            'status': 'BOGUS',
        })
        assert resp.status_code == 200
        worker = Worker.objects.get(hostname='bogus-worker')
        assert worker.status == 'IDLE'

    def test_offline_status_rejected_from_worker(
        self, make_worker_client, default_version,
    ):
        """Workers cannot set OFFLINE -- only manager derives it."""
        _, client, _ = make_worker_client('offline-attempt')
        resp = _full_heartbeat(client, 'offline-attempt', {
            'status': 'OFFLINE',
        })
        assert resp.status_code == 200
        worker = Worker.objects.get(hostname='offline-attempt')
        assert worker.status == 'IDLE'

    def test_rendering_status_accepted(
        self, make_worker_client, default_version,
    ):
        _, client, _ = make_worker_client('rendering-worker')
        resp = _full_heartbeat(client, 'rendering-worker', {
            'status': 'RENDERING',
        })
        assert resp.status_code == 200
        worker = Worker.objects.get(hostname='rendering-worker')
        assert worker.status == 'RENDERING'

    def test_missing_status_defaults_to_idle(
        self, make_worker_client, default_version,
    ):
        _, client, _ = make_worker_client('no-status-worker')
        resp = _full_heartbeat(client, 'no-status-worker')
        assert resp.status_code == 200
        worker = Worker.objects.get(hostname='no-status-worker')
        assert worker.status == 'IDLE'


@pytest.mark.django_db
class TestCpuNameSanitizationViaAPI:
    """Verify cpu_name with HTML/script chars is rejected."""

    def test_script_tag_sanitized_to_empty(
        self, make_worker_client, default_version,
    ):
        _, client, _ = make_worker_client('xss-worker')
        resp = _full_heartbeat(client, 'xss-worker', {
            'cpu_name': '<script>alert(1)</script>',
        })
        assert resp.status_code == 200
        worker = Worker.objects.get(hostname='xss-worker')
        assert worker.cpu_name == ''

    def test_valid_cpu_name_preserved(
        self, make_worker_client, default_version,
    ):
        _, client, _ = make_worker_client('good-cpu')
        resp = _full_heartbeat(client, 'good-cpu', {
            'cpu_name': 'Intel(R) Core(TM) i9-13900K @ 5.80GHz',
        })
        assert resp.status_code == 200
        worker = Worker.objects.get(hostname='good-cpu')
        assert worker.cpu_name == 'Intel(R) Core(TM) i9-13900K @ 5.80GHz'

    def test_windows_registry_format_preserved(
        self, make_worker_client, default_version,
    ):
        name = 'Intel64 Family 6 Model 154 Stepping 3, GenuineIntel'
        _, client, _ = make_worker_client('win-cpu')
        resp = _full_heartbeat(client, 'win-cpu', {'cpu_name': name})
        assert resp.status_code == 200
        worker = Worker.objects.get(hostname='win-cpu')
        assert worker.cpu_name == name


# Staleness / alias / migration-default checks live in
# ``test_worker_detail_listing.py`` to keep each file under the
# 300-line Python cap.
