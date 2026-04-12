# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for ``POST /api/enroll/`` — happy path and
post-enrollment heartbeat round-trip.

Error paths (replayed nonce, rate-limit, 503, atomic rollback) and the
structural FR-19 assertions live in the sibling files
``test_enroll_errors.py`` and ``test_enroll_rotation.py`` to keep each
file under the 300-line cap.

The tests go through the real DRF URL router — all requests use
``APIClient.post("/api/enroll/", ...)`` — so the full middleware stack,
URL pattern, and ``@api_view`` decorator chain are exercised.  Unit
tests at ``tests/unit/manager/test_enroll_view.py`` cover the same
logic at the isolated-function level; these tests cover the pieces
that only manifest under a full request lifecycle.
"""

import hashlib
import hmac
import json

import pytest
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from workers import enrollment_key
from workers.models import Worker

from .conftest import (
    TEST_ENROLLMENT_KEY_CANONICAL,
    TEST_ENROLLMENT_KEY_HYPHENATED,
    fresh_nonce,
)

User = get_user_model()

ENROLL_URL = '/api/enroll/'
HEARTBEAT_URL = '/api/heartbeat/'


def _valid_body(hostname='integration-host', nonce=None, key=None):
    """Return a valid enrollment request body."""
    return {
        'enrollment_key': key or TEST_ENROLLMENT_KEY_CANONICAL,
        'hostname': hostname,
        'nonce': nonce or fresh_nonce(),
    }


def _verify_signature(payload, signature, normalized_key):
    """Re-compute the HMAC signature using the canonical form and compare."""
    subkey = enrollment_key.derive_signing_subkey(normalized_key)
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    expected = hmac.new(subkey, canonical, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@pytest.mark.django_db
class TestEnrollHappyPath:
    """FR-10 / FR-18 happy-path through the full URL stack."""

    def test_valid_request_returns_envelope(
        self,
        seeded_enrollment_key,
        ready_runtime_state,
        reset_enroll_module_state,
    ):
        """``POST /api/enroll/`` with a valid body returns ``{payload, signature}``."""
        client = APIClient()
        body = _valid_body(hostname='happy-host')
        resp = client.post(ENROLL_URL, body, format='json')

        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {'payload', 'signature'}

        payload = data['payload']
        assert set(payload.keys()) == {
            'api_token', 'cert_fingerprint', 'manager_id', 'nonce',
        }
        assert payload['nonce'] == body['nonce']
        assert payload['manager_id'] == ready_runtime_state.manager_id
        assert payload['cert_fingerprint'] == ready_runtime_state.cert_fingerprint
        assert len(payload['api_token']) == 40  # DRF token length

    def test_signature_verifies_under_shared_subkey_derivation(
        self,
        seeded_enrollment_key,
        ready_runtime_state,
        reset_enroll_module_state,
    ):
        """Reverse-verify the HMAC signature from the manager side.

        This is the cross-boundary parity test that the unit tests only
        exercise at the isolated-function level.  Here the signature has
        travelled through the full ``@api_view`` + DRF ``Response``
        JSON-serialisation path, and we independently recompute it using
        the same ``derive_signing_subkey`` the worker uses.
        """
        client = APIClient()
        body = _valid_body(hostname='hmac-host')
        resp = client.post(ENROLL_URL, body, format='json')

        assert resp.status_code == 200
        data = resp.json()
        assert _verify_signature(
            data['payload'], data['signature'],
            TEST_ENROLLMENT_KEY_CANONICAL,
        )

    def test_hyphenated_key_accepted_through_router(
        self,
        seeded_enrollment_key,
        ready_runtime_state,
        reset_enroll_module_state,
    ):
        """``4F9X-K2PB-Q7M3-N8RT`` is normalised and accepted."""
        client = APIClient()
        body = _valid_body(
            hostname='hyphen-host',
            key=TEST_ENROLLMENT_KEY_HYPHENATED,
        )
        resp = client.post(ENROLL_URL, body, format='json')
        assert resp.status_code == 200


@pytest.mark.django_db
class TestEnrollWorkerRowCreated:
    """FR-18 (6a/6b/6c): Worker + User + Token rows appear after enrollment."""

    def test_enrollment_creates_worker_user_and_token(
        self,
        seeded_enrollment_key,
        ready_runtime_state,
        reset_enroll_module_state,
    ):
        """A fresh enrollment inserts all three rows and returns the token.

        The FR-18 rule is that ``ip_address``, ``os`` and
        ``available_tools`` are deliberately left empty at enrollment —
        the first post-enrollment heartbeat fills them in.
        """
        client = APIClient()
        body = _valid_body(hostname='new-enroll-host')
        resp = client.post(ENROLL_URL, body, format='json')

        assert resp.status_code == 200
        worker = Worker.objects.get(hostname='new-enroll-host')
        assert worker.user is not None
        assert worker.user.username == 'worker_new-enroll-host'
        assert worker.is_active is True
        # FR-18: these fields are left empty at enrollment.  ``os`` and
        # ``available_tools`` have non-NULL defaults ('' and {}) but
        # ``ip_address`` really is ``None`` because the column is nullable.
        assert worker.ip_address is None
        assert worker.os == ''
        assert worker.available_tools == {}
        # Token round-trip: the returned api_token must match the DB row.
        token = Token.objects.get(user=worker.user)
        assert resp.json()['payload']['api_token'] == token.key

    def test_existing_worker_hostname_refreshes_rather_than_duplicates(
        self,
        seeded_enrollment_key,
        ready_runtime_state,
        reset_enroll_module_state,
    ):
        """Re-enrolling an existing hostname updates the row in place."""
        client = APIClient()
        # First enrollment creates the row.
        first = client.post(
            ENROLL_URL, _valid_body(hostname='refresh-host'),
            format='json',
        )
        assert first.status_code == 200
        first_token = first.json()['payload']['api_token']

        # Second enrollment with the same hostname but a fresh nonce.
        second = client.post(
            ENROLL_URL, _valid_body(hostname='refresh-host'),
            format='json',
        )
        assert second.status_code == 200
        second_token = second.json()['payload']['api_token']
        # Token is issued via ``get_or_create`` — the same row.
        assert first_token == second_token
        # Only one Worker row exists.
        assert Worker.objects.filter(hostname='refresh-host').count() == 1


@pytest.mark.django_db
class TestEnrollTokenRoundTrip:
    """After enrollment, the returned token authenticates on ``/api/heartbeat/``."""

    def test_token_authenticates_first_heartbeat_and_populates_fields(
        self,
        seeded_enrollment_key,
        ready_runtime_state,
        reset_enroll_module_state,
        default_version,
    ):
        """Enroll, then use the returned token on a full heartbeat.

        After this round-trip the Worker row should have its ``os``,
        ``ip_address`` and ``available_tools`` populated from the
        heartbeat body (FR-18 AC-14).
        """
        client = APIClient()
        enroll_resp = client.post(
            ENROLL_URL,
            _valid_body(hostname='roundtrip-host'),
            format='json',
        )
        assert enroll_resp.status_code == 200
        api_token = enroll_resp.json()['payload']['api_token']

        # Immediately after: full-registration heartbeat using the token.
        hb_client = APIClient()
        hb_client.credentials(HTTP_AUTHORIZATION=f'Token {api_token}')
        hb_resp = hb_client.post(
            HEARTBEAT_URL,
            data={
                'hostname': 'roundtrip-host',
                'os': 'Linux',
                'ip_address': '10.1.2.3',
                'available_tools': {'blender': ['4.2.19']},
            },
            format='json',
        )
        assert hb_resp.status_code == 200

        worker = Worker.objects.get(hostname='roundtrip-host')
        assert worker.os == 'Linux'
        assert str(worker.ip_address) == '10.1.2.3'
        assert worker.available_tools == {'blender': ['4.2.19']}
