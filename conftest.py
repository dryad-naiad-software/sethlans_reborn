# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Root conftest for the Sethlans Reborn test suite.

SETHLANS_SECURITY_DEBUG=True is set via pytest.ini [env] section
(pytest-env) so the insecure default SECRET_KEY is allowed.

Provides shared fixtures for authenticated API testing.
"""

import pytest


@pytest.fixture
def admin_user(db):
    """Create and return a staff/superuser for admin-level API tests."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.create_superuser(
        username='admin', password='testpass123', email='admin@test.com',
    )
    return user


@pytest.fixture
def admin_client(admin_user):
    """Return an APIClient authenticated as an admin user."""
    from rest_framework.test import APIClient
    client = APIClient()
    client.force_authenticate(user=admin_user)
    return client


@pytest.fixture
def worker_user(db):
    """Create a worker-linked Django User with an auth token."""
    from django.contrib.auth import get_user_model
    from rest_framework.authtoken.models import Token
    from workers.models import Worker

    User = get_user_model()
    user = User.objects.create_user(
        username='worker_testhost', password='!unusable',
    )
    user.set_unusable_password()
    user.save()
    worker = Worker.objects.create(
        hostname='testhost', user=user, is_active=True,
    )
    token = Token.objects.create(user=user)
    return user, worker, token


@pytest.fixture
def worker_client(worker_user):
    """Return an APIClient authenticated as a worker via token."""
    from rest_framework.test import APIClient
    user, worker, token = worker_user
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return client
