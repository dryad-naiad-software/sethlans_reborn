# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``POST /api/enroll/`` (``manager/workers/views/enroll.py``).

Happy path, replayed nonce, invalid key, malformed body, rate-limit
exhaustion, and both 503 readiness-not-ready branches.  Each test
resets the module-level rate limiter and nonce store via a fixture so
state does not leak between tests.

Cross-boundary HMAC verification is exercised by recomputing the
signature against ``derive_signing_subkey`` using the same canonical
JSON form the view uses — this is the reverse-verify parity asserted
in the spec (FR-12a / AC-7).
"""

import base64
import hashlib
import hmac
import json
import secrets

import pytest
from rest_framework.test import APIClient

from sethlans_manager import runtime_state
from workers import enrollment_key
from workers.models import ManagerSettings
from workers.views import enroll as enroll_module


TEST_KEY_CANONICAL = "4F9XK2PBQ7M3N8RT"
TEST_HOSTNAME = "enroll-test-host"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture(autouse=True)
def _reset_enroll_module_state():
    """Clear the enroll view's module-level rate limiter and nonce store."""
    with enroll_module._rate_limiter._lock:
        enroll_module._rate_limiter._attempts.clear()
    with enroll_module._nonce_store._lock:
        enroll_module._nonce_store._seen.clear()
    yield
    with enroll_module._rate_limiter._lock:
        enroll_module._rate_limiter._attempts.clear()
    with enroll_module._nonce_store._lock:
        enroll_module._nonce_store._seen.clear()


@pytest.fixture(autouse=True)
def _ready_runtime_state():
    """Populate ``runtime_state`` so the 503 readiness check passes."""
    prev_mid = runtime_state.manager_id
    prev_fp = runtime_state.cert_fingerprint
    runtime_state.manager_id = "00000000-0000-0000-0000-000000000042"
    runtime_state.cert_fingerprint = "ab" * 32
    yield
    runtime_state.manager_id = prev_mid
    runtime_state.cert_fingerprint = prev_fp


@pytest.fixture
def seeded_enrollment_key(db):
    """Overwrite the ``ManagerSettings`` row's enrollment key to a known value."""
    row = ManagerSettings.objects.get(pk=1)
    row.enrollment_key = TEST_KEY_CANONICAL
    row.save(
        update_fields=["enrollment_key", "enrollment_key_updated_at"],
    )
    return TEST_KEY_CANONICAL


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_nonce() -> str:
    return base64.urlsafe_b64encode(
        secrets.token_bytes(16),
    ).rstrip(b"=").decode("ascii")


def _post(api_client, **body_overrides):
    body = {
        "enrollment_key": TEST_KEY_CANONICAL,
        "hostname": TEST_HOSTNAME,
        "nonce": _fresh_nonce(),
    }
    body.update(body_overrides)
    return api_client.post("/api/enroll/", body, format="json"), body


def _verify_signature(payload: dict, signature: str, normalized_key: str) -> bool:
    subkey = enrollment_key.derive_signing_subkey(normalized_key)
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    expected = hmac.new(subkey, canonical, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# Happy path (AC-7)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestHappyPath:
    def test_valid_request_returns_200_envelope(
        self, api_client, seeded_enrollment_key,
    ):
        resp, body = _post(api_client)
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"payload", "signature"}
        payload = data["payload"]
        assert set(payload.keys()) == {
            "api_token", "cert_fingerprint", "manager_id", "nonce",
        }
        assert payload["nonce"] == body["nonce"]
        assert payload["manager_id"] == runtime_state.manager_id
        assert payload["cert_fingerprint"] == runtime_state.cert_fingerprint
        assert len(payload["api_token"]) == 40  # DRF Token length
        # HMAC reverse-verify using the same derive_signing_subkey the
        # worker uses — this is the cross-boundary parity assertion.
        assert _verify_signature(
            payload, data["signature"], TEST_KEY_CANONICAL,
        )

    def test_hyphenated_key_accepted_and_normalized(
        self, api_client, seeded_enrollment_key,
    ):
        resp, _ = _post(
            api_client, enrollment_key="4F9X-K2PB-Q7M3-N8RT",
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Replayed nonce (AC-9)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReplayedNonce:
    def test_second_request_same_nonce_returns_400(
        self, api_client, seeded_enrollment_key,
    ):
        nonce = _fresh_nonce()
        first, _ = _post(api_client, nonce=nonce)
        assert first.status_code == 200
        second, _ = _post(api_client, nonce=nonce)
        assert second.status_code == 400
        assert second.json() == {"detail": "Replayed nonce."}


# ---------------------------------------------------------------------------
# Invalid key (AC-8)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestInvalidKey:
    def test_wrong_key_returns_403_and_increments_limiter(
        self, api_client, seeded_enrollment_key,
    ):
        resp, _ = _post(
            api_client, enrollment_key="00000000000000PP",  # valid shape
        )
        assert resp.status_code == 403
        assert resp.json() == {"detail": "Invalid enrollment key."}
        # Rate limiter should have at least one recorded attempt.
        with enroll_module._rate_limiter._lock:
            assert len(enroll_module._rate_limiter._attempts) == 1


# ---------------------------------------------------------------------------
# Malformed body (AC-11, AC-12, AC-13)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMalformedBody:
    def test_missing_enrollment_key_returns_400(
        self, api_client, seeded_enrollment_key,
    ):
        resp = api_client.post(
            "/api/enroll/",
            {"hostname": TEST_HOSTNAME, "nonce": _fresh_nonce()},
            format="json",
        )
        assert resp.status_code == 400
        assert "enrollment_key" in resp.json()

    def test_bad_hostname_returns_400(
        self, api_client, seeded_enrollment_key,
    ):
        resp, _ = _post(api_client, hostname="host with space")
        assert resp.status_code == 400
        assert "hostname" in resp.json()

    def test_bad_nonce_returns_400(
        self, api_client, seeded_enrollment_key,
    ):
        resp, _ = _post(api_client, nonce="too-short")
        assert resp.status_code == 400
        assert "nonce" in resp.json()

    def test_not_json_returns_400(
        self, api_client, seeded_enrollment_key,
    ):
        resp = api_client.post(
            "/api/enroll/", "not-a-json-object",
            content_type="application/json",
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Rate limit exhaustion (AC-10)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRateLimit:
    def test_five_invalid_attempts_then_429(
        self, api_client, seeded_enrollment_key,
    ):
        for _ in range(5):
            resp, _ = _post(
                api_client, enrollment_key="00000000000000PP",
            )
            assert resp.status_code == 403
        # The 6th attempt should be rate-limited regardless of key
        # validity (limit check comes first in the view).
        resp, _ = _post(
            api_client, enrollment_key="00000000000000PP",
        )
        assert resp.status_code == 429
        assert "Too many" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 503 readiness (AC-14b / AC-43)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReadinessNotReady:
    def test_manager_id_none_returns_503(
        self, api_client, seeded_enrollment_key,
    ):
        runtime_state.manager_id = None
        resp, _ = _post(api_client)
        assert resp.status_code == 503
        assert resp.json() == {"detail": "Enrollment service not ready."}
        # 503 must NOT consume rate-limiter budget.
        with enroll_module._rate_limiter._lock:
            assert len(enroll_module._rate_limiter._attempts) == 0

    def test_cert_fingerprint_none_returns_503(
        self, api_client, seeded_enrollment_key,
    ):
        runtime_state.cert_fingerprint = None
        resp, _ = _post(api_client)
        assert resp.status_code == 503
        with enroll_module._rate_limiter._lock:
            assert len(enroll_module._rate_limiter._attempts) == 0
