# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for the enrollment-rotation endpoint and the FR-19
structural cleanup of ``WorkerHeartbeatViewSet``.

Covers:

  * ``test_regenerate_enrollment_key_unauthenticated`` — the rotation
    endpoint keeps its ``IsAdmin`` permission.
  * ``test_regenerate_enrollment_key_admin`` — an admin session-authed
    client can rotate the key and the DB row is updated to the new
    canonical form.
  * ``test_rotate_then_old_key_rejected_and_new_key_accepted`` — rotation
    via the management command is observed by the enroll endpoint on
    the very next request (no caching).
  * ``test_heartbeat_viewset_has_no_permission_override`` — AC-21a
    structural regression test that ``get_permissions`` /
    ``get_authenticators`` are not present as instance methods on
    ``WorkerHeartbeatViewSet`` after the FR-19 cleanup.
"""

import pytest
from django.core.management import call_command
from rest_framework import viewsets
from rest_framework.test import APIClient

from workers import enrollment_key
from workers.models import ManagerSettings
from workers.views.heartbeat import WorkerHeartbeatViewSet

from .conftest import (
    TEST_ENROLLMENT_KEY_CANONICAL,
    fresh_nonce,
)

REGEN_URL = '/api/auth/regenerate_enrollment_key/'
ENROLL_URL = '/api/enroll/'


def _enroll_body(hostname, key):
    return {
        'enrollment_key': key,
        'hostname': hostname,
        'nonce': fresh_nonce(),
    }


@pytest.mark.django_db
class TestRegenerateEnrollmentKeyEndpoint:
    """FR-17: ``POST /api/auth/regenerate_enrollment_key/`` is admin-only."""

    def test_regenerate_enrollment_key_unauthenticated(self):
        """Anonymous client → 401 or 403 (``IsAdmin`` permission)."""
        client = APIClient()
        resp = client.post(REGEN_URL)
        assert resp.status_code in (401, 403)

    def test_regenerate_enrollment_key_non_admin(self, db):
        """Non-staff authenticated user still rejected."""
        from django.contrib.auth import get_user_model
        User = get_user_model()
        user = User.objects.create_user(
            username='nobody', password='pw',
        )
        client = APIClient()
        client.force_authenticate(user=user)
        resp = client.post(REGEN_URL)
        assert resp.status_code == 403

    def test_regenerate_enrollment_key_admin(
        self, admin_client, seeded_enrollment_key,
    ):
        """An admin session-authed client can rotate the key.

        The response includes an ``enrollment_key`` field in the
        hyphenated display form, and the DB row is updated to the new
        canonical form.
        """
        old_key = ManagerSettings.objects.get(pk=1).enrollment_key
        assert old_key == TEST_ENROLLMENT_KEY_CANONICAL

        resp = admin_client.post(REGEN_URL)
        assert resp.status_code == 200
        display_key = resp.data['enrollment_key']
        # 19 chars = 16 base32 chars + 3 hyphens.
        assert len(display_key) == 19
        assert display_key.count('-') == 3

        # The display form must normalise cleanly to a new canonical key.
        new_canonical = enrollment_key.normalize(display_key)
        assert new_canonical != old_key
        # DB row is the source of truth.
        row = ManagerSettings.objects.get(pk=1)
        assert row.enrollment_key == new_canonical


@pytest.mark.django_db
class TestRotationVisibleToEnrollEndpoint:
    """FR-14: no in-memory cache — rotation takes effect on the next request."""

    def test_rotate_then_old_key_rejected_and_new_key_accepted(
        self,
        seeded_enrollment_key,
        ready_runtime_state,
        reset_enroll_module_state,
    ):
        """Rotate via the management command, then try old + new keys."""
        client = APIClient()
        # Baseline: the seeded key works.
        ok = client.post(
            ENROLL_URL,
            _enroll_body('pre-rotate', TEST_ENROLLMENT_KEY_CANONICAL),
            format='json',
        )
        assert ok.status_code == 200

        # Rotate via the management command — exactly what an admin
        # would run on a secondary manager.  ``call_command`` captures
        # stdout so the secret never appears in the test output.
        from io import StringIO
        stdout = StringIO()
        stderr = StringIO()
        call_command(
            'rotate_enrollment_key', stdout=stdout, stderr=stderr,
        )
        new_display = stdout.getvalue().strip()
        assert len(new_display) == 19
        new_canonical = enrollment_key.normalize(new_display)

        # Old key is now rejected with 403.
        bad = client.post(
            ENROLL_URL,
            _enroll_body('after-rotate-old', TEST_ENROLLMENT_KEY_CANONICAL),
            format='json',
        )
        assert bad.status_code == 403
        assert bad.json() == {'detail': 'Invalid enrollment key.'}

        # New key is accepted.
        good = client.post(
            ENROLL_URL,
            _enroll_body('after-rotate-new', new_canonical),
            format='json',
        )
        assert good.status_code == 200


class TestHeartbeatViewsetStructuralCleanup:
    """AC-21a: the FR-19 cleanup of the heartbeat viewset is permanent.

    The spec demands that ``get_permissions`` and ``get_authenticators``
    are NOT overridden on ``WorkerHeartbeatViewSet`` — they must come
    only via inheritance from DRF's base classes.  This is a structural
    regression test that a future refactor doesn't quietly re-introduce
    the overrides.
    """

    def test_heartbeat_viewset_has_no_permission_override(self):
        """``get_permissions`` and ``get_authenticators`` come from ViewSet."""
        # ``__dict__`` only contains attributes defined ON this class,
        # not inherited from a base class — so if the class overrides
        # either method it will appear here.
        own_attrs = set(WorkerHeartbeatViewSet.__dict__.keys())
        assert 'get_permissions' not in own_attrs
        assert 'get_authenticators' not in own_attrs

        # Meanwhile the class-level attribute form IS present — this is
        # the positive half of the structural invariant.
        assert 'authentication_classes' in own_attrs
        assert 'permission_classes' in own_attrs

        # And the methods are still callable via the inheritance chain
        # (sanity check that DRF will still honour them).
        instance = WorkerHeartbeatViewSet()
        assert callable(instance.get_permissions)
        assert callable(instance.get_authenticators)
        # And they come from the DRF ViewSet base, not from the subclass.
        assert (
            WorkerHeartbeatViewSet.get_permissions
            is viewsets.ViewSet.get_permissions
        )
        assert (
            WorkerHeartbeatViewSet.get_authenticators
            is viewsets.ViewSet.get_authenticators
        )
