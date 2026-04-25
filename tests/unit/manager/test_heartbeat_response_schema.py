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

The ``token`` field is the one documented exception: it is only
present on the full-registration branch (when the request body
includes an ``os`` field), so it is declared ``required=False`` on the
serializer and the two paths are exercised separately.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from workers.models import Worker
from workers.services.sentinel import create_sentinel
from workers.views import heartbeat as heartbeat_mod
from workers.views.heartbeat_schema import WorkerHeartbeatResponseSerializer

User = get_user_model()

HEARTBEAT_URL = '/api/heartbeat/'

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures (mirror tests/unit/manager/test_heartbeat_setup_complete.py so
# the same auth + sentinel setup is exercised here).
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
    """Return (hostname, client) for a token-authed worker that has NOT
    yet been registered (no Worker row).  Drives the full-registration
    branch — the first heartbeat from a freshly enrolled worker carries
    an ``os`` field and creates the Worker row plus a ``token`` field
    on the response envelope.
    """
    user = User.objects.create_user(username='worker_schemafresh')
    user.set_unusable_password()
    user.save()
    token = Token.objects.create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return 'schemafresh', client


@pytest.fixture(autouse=True)
def _reset_heartbeat_setup_cache():
    """Reset the sticky-True setup-complete cache around every test.

    The heartbeat view caches a True observation of
    ``is_setup_complete`` at module scope.  Reset before AND after to
    guarantee no leakage in either direction (other test files in this
    run can also pollute the flag).
    """
    heartbeat_mod._reset_setup_complete_cache()
    yield
    heartbeat_mod._reset_setup_complete_cache()


@pytest.fixture
def patch_data_dir(mocker, tmp_path):
    """Pin heartbeat's ``_get_data_dir`` to ``tmp_path``."""
    mocker.patch.object(
        heartbeat_mod, '_get_data_dir', return_value=tmp_path,
    )
    return tmp_path


# ---------------------------------------------------------------------------
# Test 2 — fast smoke test (no Django test client)
# ---------------------------------------------------------------------------


class TestHeartbeatResponseSerializerSmoke:
    """Quick "is the serializer importable and well-formed" sanity check.

    Runs without the test client, so a regression here surfaces faster
    than the schema-shape tests below.
    """

    def test_serializer_declares_extra_response_fields(self):
        """The three heartbeat-only fields MUST be on the serializer."""
        fields = WorkerHeartbeatResponseSerializer().fields
        assert 'token' in fields
        assert 'required_blender_versions' in fields
        assert 'manager_setup_complete' in fields

    def test_token_field_is_optional(self):
        """``token`` is only present on full-registration responses, so
        the serializer must mark it optional + nullable (otherwise
        every plain-heartbeat response violates the documented schema).
        """
        token_field = WorkerHeartbeatResponseSerializer().fields['token']
        assert token_field.required is False
        assert token_field.allow_null is True

    def test_inherits_worker_serializer_fields(self):
        """The response envelope is the WorkerSerializer payload plus
        the three injected keys — losing the inheritance would silently
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
    documented schema promises clients will receive.  ``token`` is
    excluded because it is intentionally optional (only emitted on the
    full-registration branch).
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
        self, worker_with_token, patch_data_dir,
    ):
        """Plain-heartbeat path (no ``os`` payload).

        ``token`` MUST NOT appear on this response — the serializer
        marks it ``required=False`` precisely because of this branch.
        """
        worker, client = worker_with_token
        # Use a checkpoint sentinel so manager_setup_complete=False, but
        # the schema-shape assertions are independent of its value.
        create_sentinel(
            patch_data_dir, 'manager', ['topology_chosen'],
        )

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
        # are actually emitted (token excluded — see docstring).
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
            "Plain-heartbeat response leaked a 'token' field — that "
            "field is full-registration-only."
        )

    def test_full_registration_response_keys_match_serializer(
        self, fresh_worker_client, patch_data_dir,
    ):
        """Full-registration path (payload includes ``os``).

        ``token`` MUST appear on this response (it is the freshly
        minted DRF auth token key the worker will use for subsequent
        heartbeats).
        """
        hostname, client = fresh_worker_client
        create_sentinel(patch_data_dir, 'manager', ['verified'])

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
        assert Worker.objects.filter(hostname=hostname).exists()

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

        # Branch-specific: token MUST be present on full registration.
        assert 'token' in actual_keys, (
            "Full-registration response is missing 'token' — workers "
            "rely on this to authenticate subsequent heartbeats."
        )
        assert resp.data['token'], "'token' field present but empty/falsy."
