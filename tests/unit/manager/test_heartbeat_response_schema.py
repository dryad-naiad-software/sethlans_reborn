# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Schema-shape regression tests for the heartbeat response (issue #131).

The heartbeat response is assembled as a plain dict in
``WorkerHeartbeatViewSet._process_heartbeat`` /
``_handle_full_registration``, but is documented for OpenAPI via
``WorkerHeartbeatResponseSerializer``.  Drift between the two surfaces
silently breaks the published schema and any client that relies on it.

These tests assert the keysets of the actual response and the
serializer's declared fields stay in sync:

* Every key the runtime returns MUST be documented on the serializer
  (catches "added a field, forgot to document it").
* Every required field on the serializer MUST appear in the runtime
  response (catches "documented a field, forgot to send it").

Both branches (plain heartbeat and full registration, when the request
body includes an ``os`` field) are exercised separately.  Neither
branch returns an API token: the former full-registration ``token``
field was removed in issue #215 because the caller already holds its
own token from enrollment.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from workers.models import Worker
from workers.views.heartbeat_schema import WorkerHeartbeatResponseSerializer

User = get_user_model()

HEARTBEAT_URL = '/api/heartbeat/'

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures: a real Worker + token, because heartbeat is a
# token-authenticated DB-backed view.
# ---------------------------------------------------------------------------


@pytest.fixture
def worker_with_token():
    """Create a Worker, linked User, and APIClient with token credentials."""
    user = User.objects.create_user(username='worker_schemaregress')
    user.set_unusable_password()
    user.save()
    worker = Worker.objects.create(
        hostname='schemaregress',
        user=user,
        is_active=True,
        available_tools={'blender': ['4.2.19']},
    )
    token = Token.objects.create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return worker, client


@pytest.fixture
def fresh_worker_client():
    """Return (hostname, client) for a freshly enrolled worker that has
    NOT yet sent its full registration.

    Mirrors what ``POST /api/enroll/`` leaves behind: a User, a Token
    and a bare Worker row.  Drives the full-registration branch — the
    first heartbeat from a freshly enrolled worker carries an ``os``
    field and fills the row in.
    """
    user = User.objects.create_user(username='worker_schemafresh')
    user.set_unusable_password()
    user.save()
    Worker.objects.create(hostname='schemafresh', user=user, is_active=True)
    token = Token.objects.create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return 'schemafresh', client


# ---------------------------------------------------------------------------
# Test 2 — fast smoke test (no Django test client)
# ---------------------------------------------------------------------------


class TestHeartbeatResponseSerializerSmoke:
    """Quick "is the serializer importable and well-formed" sanity check.

    Runs without the test client, so a regression here surfaces faster
    than the schema-shape tests below.
    """

    def test_serializer_declares_extra_response_fields(self):
        """The heartbeat-only fields MUST be on the serializer."""
        fields = WorkerHeartbeatResponseSerializer().fields
        assert 'required_blender_versions' in fields
        assert 'dismissed_card_alerts' in fields

    def test_serializer_does_not_document_manager_setup_complete(self):
        """Issue #304: the setup-complete flag is gone from the schema."""
        fields = WorkerHeartbeatResponseSerializer().fields
        assert 'manager_setup_complete' not in fields

    def test_extra_fields_are_exactly_the_heartbeat_additions(self):
        """Beyond WorkerSerializer the envelope adds exactly these two."""
        from workers.serializers import WorkerSerializer

        extra = (
            set(WorkerHeartbeatResponseSerializer().fields)
            - set(WorkerSerializer().fields)
        )
        assert extra == {'required_blender_versions', 'dismissed_card_alerts'}

    def test_serializer_does_not_document_a_token(self):
        """The heartbeat response never carries an API token (#215)."""
        assert 'token' not in WorkerHeartbeatResponseSerializer().fields

    def test_inherits_worker_serializer_fields(self):
        """The response envelope is the WorkerSerializer payload plus
        the injected keys — losing the inheritance would silently
        drop every Worker field from the documented schema.
        """
        from workers.serializers import WorkerSerializer

        base_fields = set(WorkerSerializer().fields.keys())
        response_fields = set(
            WorkerHeartbeatResponseSerializer().fields.keys(),
        )
        # Every WorkerSerializer field must still be present.
        missing = base_fields - response_fields
        assert not missing, (
            f"WorkerHeartbeatResponseSerializer dropped base fields: "
            f"{sorted(missing)}"
        )


# ---------------------------------------------------------------------------
# Test 1 — schema-shape drift catcher (real heartbeat round-trip)
# ---------------------------------------------------------------------------


def _serializer_field_names() -> set[str]:
    """All declared fields on WorkerHeartbeatResponseSerializer."""
    return set(WorkerHeartbeatResponseSerializer().fields.keys())


def _required_serializer_field_names() -> set[str]:
    """Fields marked required=True on WorkerHeartbeatResponseSerializer.

    Used to assert the runtime response carries every field the
    documented schema promises clients will receive.
    """
    return {
        name
        for name, field in WorkerHeartbeatResponseSerializer().fields.items()
        if field.required
    }


class TestHeartbeatResponseShapeMatchesSchema:
    """Drift catcher: response keys vs. serializer fields.

    The two assertions in each test below are the contract:

    * ``actual - documented`` MUST be empty — every key the runtime
      sends has to be declared on the serializer, otherwise the
      published OpenAPI schema lies about the response shape.
    * ``required_documented - actual`` MUST be empty — every field the
      serializer says is required has to actually appear in the
      runtime response, otherwise the schema promises something the
      server never delivers.
    """

    def test_plain_heartbeat_response_keys_match_serializer(
        self, worker_with_token,
    ):
        """Plain-heartbeat path (no ``os`` payload).

        ``token`` MUST NOT appear on this response.
        """
        worker, client = worker_with_token

        resp = client.post(
            HEARTBEAT_URL,
            data={'hostname': worker.hostname},
            format='json',
        )

        assert resp.status_code == 200
        actual_keys = set(resp.data.keys())
        documented_keys = _serializer_field_names()

        # Key contract assertion #1 — no undocumented runtime fields.
        undocumented = actual_keys - documented_keys
        assert not undocumented, (
            f"Heartbeat response has fields not documented on "
            f"WorkerHeartbeatResponseSerializer: {sorted(undocumented)}. "
            f"Add them to manager/workers/views/heartbeat_schema.py."
        )

        # Key contract assertion #2 — all required documented fields
        # are actually emitted.
        required_documented = _required_serializer_field_names()
        missing_required = required_documented - actual_keys
        assert not missing_required, (
            f"Heartbeat response is missing fields the serializer "
            f"declares as required: {sorted(missing_required)}. "
            f"Either emit them in _process_heartbeat or relax their "
            f"required flag."
        )

        # Branch-specific: token MUST be absent on the heartbeat path.
        assert 'token' not in actual_keys, (
            "Plain-heartbeat response leaked a 'token' field."
        )

    def test_full_registration_response_keys_match_serializer(
        self, fresh_worker_client,
    ):
        """Full-registration path (payload includes ``os``).

        ``token`` MUST NOT appear on this response: the worker already
        holds its token from enrollment, and echoing it back served no
        consumer (issue #215).
        """
        hostname, client = fresh_worker_client

        resp = client.post(
            HEARTBEAT_URL,
            data={
                'hostname': hostname,
                'os': 'Linux',
                'ip_address': '192.168.1.51',
                'available_tools': {'blender': ['4.2.19']},
            },
            format='json',
        )

        assert resp.status_code == 200
        # Sanity: the full-registration branch ran.
        assert Worker.objects.get(hostname=hostname).os == 'Linux'

        actual_keys = set(resp.data.keys())
        documented_keys = _serializer_field_names()

        # Key contract assertion #1 — no undocumented runtime fields.
        undocumented = actual_keys - documented_keys
        assert not undocumented, (
            f"Full-registration response has fields not documented on "
            f"WorkerHeartbeatResponseSerializer: {sorted(undocumented)}. "
            f"Add them to manager/workers/views/heartbeat_schema.py."
        )

        # Key contract assertion #2 — all required documented fields
        # are actually emitted.
        required_documented = _required_serializer_field_names()
        missing_required = required_documented - actual_keys
        assert not missing_required, (
            f"Full-registration response is missing fields the "
            f"serializer declares as required: "
            f"{sorted(missing_required)}."
        )

        # Branch-specific: no token on full registration either (#215).
        assert 'token' not in actual_keys, (
            "Full-registration response leaked a 'token' field."
        )
