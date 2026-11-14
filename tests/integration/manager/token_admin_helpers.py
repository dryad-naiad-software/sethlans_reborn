# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared helpers for the issue #220 tests (admin-only worker token
actions and worker listing): ``test_token_actions_permissions.py`` and
``test_worker_admin_hardening.py``.
"""

from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

User = get_user_model()

HEARTBEAT_URL = '/api/heartbeat/'
ENROLL_URL = '/api/enroll/'
CSRF_URL = '/api/auth/csrf/'
TOKEN_ACTIONS = ('revoke_token', 'regenerate_token', 'force_reenroll')


def action_url(worker, action_name):
    """Return the detail URL of a token action for *worker*."""
    return f'{HEARTBEAT_URL}{worker.pk}/{action_name}/'


def token_key(worker):
    """Return the worker's current token key, or None if it has none."""
    return (
        Token.objects.filter(user=worker.user)
        .values_list('key', flat=True)
        .first()
    )


def session_client(username, password, is_staff, enforce_csrf=False):
    """Return an APIClient logged in through a real Django session.

    The user is created with a usable password so ``client.login``
    goes through ``SessionAuthentication`` exactly like the dashboard.
    """
    User.objects.create_user(
        username=username, password=password, is_staff=is_staff,
    )
    client = APIClient(enforce_csrf_checks=enforce_csrf)
    assert client.login(username=username, password=password)
    return client
