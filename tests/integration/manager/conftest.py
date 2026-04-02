# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared fixtures for manager integration tests.

All fixtures produce real Django model instances using the test database.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from workers.models import (
    SupportedBlenderVersion, Project, Asset, Worker,
)

User = get_user_model()


@pytest.fixture
def admin_user(db):
    """Create and return a staff/superuser for admin-level API tests."""
    return User.objects.create_superuser(
        username='admin',
        password='testpass123',
        email='admin@test.com',
    )


@pytest.fixture
def admin_client(admin_user):
    """Return an APIClient force-authenticated as an admin user."""
    client = APIClient()
    client.force_authenticate(user=admin_user)
    return client


@pytest.fixture
def default_version(db):
    """
    Create or retrieve a default SupportedBlenderVersion.

    Uses get_or_create to avoid migration conflicts when multiple
    tests need a version with the same series.
    """
    version, _ = SupportedBlenderVersion.objects.get_or_create(
        series='4.2',
        defaults={
            'resolved_version': '4.2.19',
            'is_default': True,
        },
    )
    return version


@pytest.fixture
def project(admin_client, default_version):
    """Create a Project linked to the default Blender version."""
    return Project.objects.create(
        name='IntegrationProject',
        blender_version=default_version,
    )


@pytest.fixture
def asset(project):
    """Create an Asset with a minimal .blend file stub."""
    blend_bytes = b'\x00' * 64
    asset = Asset(
        project=project,
        name='TestAsset001',
        blend_file=ContentFile(blend_bytes, name='test.blend'),
    )
    asset.save()
    return asset


@pytest.fixture
def worker_with_token(db):
    """
    Create a Worker, linked User, and auth Token.

    Returns a (worker, client) tuple where client is an APIClient
    with token credentials already configured.
    """
    user = User.objects.create_user(
        username='worker_inthost',
    )
    user.set_unusable_password()
    user.save()
    worker = Worker.objects.create(
        hostname='inthost',
        user=user,
        is_active=True,
        available_tools={'blender': ['4.2.19']},
    )
    token = Token.objects.create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return worker, client
