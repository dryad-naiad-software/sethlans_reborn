# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared helpers for the issue #215 heartbeat caller-binding tests
(``test_heartbeat_binding.py`` and ``test_heartbeat_binding_flows.py``).
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from workers.models import Worker

User = get_user_model()

HEARTBEAT_URL = '/api/heartbeat/'
ENROLL_URL = '/api/enroll/'

# A full-registration body (``os`` present) with values that differ
# from what ``make_worker`` stores, so any write would be visible.
FULL_REGISTRATION = {
    'os': 'Windows',
    'ip_address': '10.9.9.9',
    'available_tools': {'blender': ['9.9.9']},
    'cpu_name': 'Attacker CPU',
    'ui_url': 'https://10.9.9.9:7443',
    'status': 'RENDERING',
}


def make_worker(hostname):
    """Create an enrolled worker (User + Token + Worker) and its client.

    Returns ``(worker, token, client)`` where *client* carries the
    worker's ``Authorization: Token`` header.
    """
    user = User.objects.create_user(username=f'worker_{hostname}')
    user.set_unusable_password()
    user.save()
    worker = Worker.objects.create(
        hostname=hostname,
        user=user,
        is_active=True,
        os='Linux',
        status='IDLE',
        ui_url=f'https://{hostname}.lan:7443',
        available_tools={'blender': ['4.2.19']},
    )
    token = Token.objects.create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return worker, token, client


def make_two_workers():
    """Two enrolled workers, A (``host-a``) and B (``host-b``).

    B's ``last_seen`` is backdated an hour so any heartbeat write to B
    would visibly change it.
    """
    a = make_worker('host-a')
    b = make_worker('host-b')
    Worker.objects.filter(pk=b[0].pk).update(
        last_seen=b[0].last_seen - timedelta(hours=1),
    )
    return a, b


def snapshot(worker):
    """Return the heartbeat-driven fields of *worker*, fresh from the DB."""
    worker.refresh_from_db()
    return {
        'last_seen': worker.last_seen,
        'status': worker.status,
        'ui_url': worker.ui_url,
        'os': worker.os,
        'ip_address': worker.ip_address,
        'available_tools': worker.available_tools,
        'cpu_name': worker.cpu_name,
        'user_id': worker.user_id,
    }
