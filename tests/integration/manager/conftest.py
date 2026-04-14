# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared fixtures for manager integration tests.

All fixtures produce real Django model instances using the test database.
"""

import base64
import secrets

import pytest
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from workers.models import (
    SupportedBlenderVersion, Project, Asset, Worker,
)

User = get_user_model()

# Canonical test enrollment key (16-char Crockford base32 form).  Mirrors the
# value pinned in ``tests/unit/manager/test_enroll_view.py`` so tests that
# cross the unit/integration boundary stay in lock-step.
TEST_ENROLLMENT_KEY_CANONICAL = "4F9XK2PBQ7M3N8RT"
TEST_ENROLLMENT_KEY_HYPHENATED = "4F9X-K2PB-Q7M3-N8RT"


def fresh_nonce() -> str:
    """Return a fresh urlsafe-base64 nonce (22 chars, 16 bytes decoded)."""
    return base64.urlsafe_b64encode(
        secrets.token_bytes(16),
    ).rstrip(b"=").decode("ascii")


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
    blend_bytes = b'BLENDER' + b'\x00' * 57
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


@pytest.fixture
def make_worker_client(db):
    """Return a factory that builds a fresh token-authed Worker + APIClient.

    Each call creates a new ``User``, a linked ``Worker`` row for the given
    hostname, and a DRF ``Token`` whose value is pre-wired onto the returned
    ``APIClient`` via ``HTTP_AUTHORIZATION``.  Use this for tests that need
    to exercise the token-authenticated heartbeat path without going
    through ``POST /api/enroll/``.
    """
    def _make(hostname: str):
        user = User.objects.create_user(username=f'worker_{hostname}')
        user.set_unusable_password()
        user.save()
        worker = Worker.objects.create(
            hostname=hostname,
            user=user,
            is_active=True,
        )
        token = Token.objects.create(user=user)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
        return worker, client, token
    return _make


@pytest.fixture
def seeded_enrollment_key(db):
    """Pin the ``ManagerSettings`` enrollment key to a known canonical value.

    Tests that send valid requests to ``POST /api/enroll/`` depend on the
    DB-backed enrollment key matching ``TEST_ENROLLMENT_KEY_CANONICAL``.
    The migration seeds a random key, so each test that needs a known
    value uses this fixture to overwrite the row.
    """
    from workers.models import ManagerSettings
    row = ManagerSettings.objects.get(pk=1)
    row.enrollment_key = TEST_ENROLLMENT_KEY_CANONICAL
    row.save(
        update_fields=["enrollment_key", "enrollment_key_updated_at"],
    )
    return TEST_ENROLLMENT_KEY_CANONICAL


@pytest.fixture
def ready_runtime_state():
    """Populate ``runtime_state`` so the enroll endpoint's readiness check passes.

    ``runtime_state`` is a module-level singleton with ``manager_id`` and
    ``cert_fingerprint`` fields — both default to ``None`` in the test
    process, which would otherwise trigger the 503 branch.  Save/restore
    the previous values via a yield so stacked tests don't leak state.
    """
    from sethlans_manager import runtime_state
    prev_mid = runtime_state.manager_id
    prev_fp = runtime_state.cert_fingerprint
    runtime_state.manager_id = "00000000-0000-0000-0000-000000000042"
    runtime_state.cert_fingerprint = "ab" * 32
    yield runtime_state
    runtime_state.manager_id = prev_mid
    runtime_state.cert_fingerprint = prev_fp


@pytest.fixture
def reset_enroll_module_state():
    """Clear the enroll view's rate limiter and nonce store between tests.

    Both are process-local in-memory singletons — transactional rollback
    does not reset them.  Tests that enroll multiple workers or that
    exercise the rate-limit/nonce-replay paths need a clean slate.
    """
    from workers.views import enroll as enroll_module
    with enroll_module._rate_limiter._lock:
        enroll_module._rate_limiter._attempts.clear()
    with enroll_module._nonce_store._lock:
        enroll_module._nonce_store._seen.clear()
    yield enroll_module
    with enroll_module._rate_limiter._lock:
        enroll_module._rate_limiter._attempts.clear()
    with enroll_module._nonce_store._lock:
        enroll_module._nonce_store._seen.clear()
