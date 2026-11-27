# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``POST /api/enroll/`` (``manager/workers/views/enroll.py``).

The body is the setup-rebuild FR-WK-8 key proof ``{hostname, nonce, proof}``.
Happy path, replayed nonce, wrong proof, refused bodies and rate-limit
exhaustion.  Each test resets the module-level rate limiter and nonce store
via a fixture so state does not leak between tests.  The request order
(including readiness) is pinned in ``test_enroll_order.py``; the field
shapes in ``test_enrollment_serializer.py``.
"""

import base64
import hashlib
import hmac
import json

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from sethlans_manager import runtime_state
from tests.unit.manager._enroll_body import KEY, OTHER_KEY, enroll_body
from workers import enrollment_key
from workers.models import ManagerSettings
from workers.serializers_enrollment import LEGACY_KEY_DETAIL
from workers.views import enroll as enroll_module

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
    row.enrollment_key = KEY
    row.save(
        update_fields=["enrollment_key", "enrollment_key_updated_at"],
    )
    return KEY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _post(api_client, body):
    return api_client.post("/api/enroll/", body, format="json")


def _limiter_attempts():
    with enroll_module._rate_limiter._lock:
        return len(enroll_module._rate_limiter._attempts)


def _verify_signature(payload: dict, signature: str) -> bool:
    subkey = enrollment_key.derive_signing_subkey(KEY)
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    expected = hmac.new(subkey, canonical, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _decrypt_token(payload: dict) -> str:
    aad = (payload["nonce"] + "\n" + payload["manager_id"]).encode("utf-8")
    plain = AESGCM(enrollment_key.derive_token_subkey(KEY)).decrypt(
        bytes.fromhex(payload["api_token_nonce"]),
        base64.b64decode(payload["api_token_ct"]),
        aad,
    )
    return plain.decode("ascii")


# ---------------------------------------------------------------------------
# Happy path (AC-7, FR-WK-8)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestHappyPath:
    def test_valid_proof_returns_signed_envelope_with_encrypted_token(
        self, api_client, seeded_enrollment_key,
    ):
        """AC-7 / FR-WK-8: signed envelope; the issued token travels only encrypted."""
        body = enroll_body(TEST_HOSTNAME)
        resp = _post(api_client, body)
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"payload", "signature"}
        payload = data["payload"]
        assert set(payload.keys()) == {
            "api_token_ct", "api_token_nonce", "cert_fingerprint",
            "manager_id", "nonce",
        }
        assert payload["nonce"] == body["nonce"]
        assert payload["manager_id"] == runtime_state.manager_id
        assert payload["cert_fingerprint"] == runtime_state.cert_fingerprint
        assert _verify_signature(payload, data["signature"])
        token = _decrypt_token(payload)
        assert Token.objects.filter(key=token).exists()
        assert token not in resp.content.decode("utf-8")


# ---------------------------------------------------------------------------
# Replayed nonce (AC-9)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReplayedNonce:
    def test_second_request_same_nonce_returns_400(
        self, api_client, seeded_enrollment_key,
    ):
        """AC-9: a nonce is accepted once."""
        body = enroll_body(TEST_HOSTNAME)
        assert _post(api_client, body).status_code == 200
        second = _post(api_client, body)
        assert second.status_code == 400
        assert second.json() == {"detail": "Replayed nonce."}


# ---------------------------------------------------------------------------
# Wrong proof (AC-8, FR-WK-8)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestWrongProof:
    @pytest.mark.parametrize("body_kwargs", [
        {"key": OTHER_KEY},
        {"proof_hostname": "someone-else"},
    ], ids=["other-key", "proof-for-other-hostname"])
    def test_wrong_proof_returns_403_and_counts_against_the_limit(
        self, api_client, seeded_enrollment_key, body_kwargs,
    ):
        """AC-8 / FR-WK-8: a proof from another key, or over another hostname, is refused."""
        resp = _post(api_client, enroll_body(TEST_HOSTNAME, **body_kwargs))
        assert resp.status_code == 403
        assert resp.json() == {"detail": "Invalid enrollment key."}
        assert _limiter_attempts() == 1


# ---------------------------------------------------------------------------
# Refused bodies (AC-11..13, FR-WK-8)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRefusedBody:
    def test_body_with_enrollment_key_gets_the_update_message(
        self, api_client, seeded_enrollment_key,
    ):
        """FR-WK-8: an old worker's body (key in clear) gets 400 telling it to update."""
        old_body = {
            "enrollment_key": KEY,
            "hostname": TEST_HOSTNAME,
            "nonce": "A" * 22,
        }
        resp = _post(api_client, old_body)
        assert resp.status_code == 400
        assert resp.json() == {"detail": LEGACY_KEY_DETAIL}
        assert _limiter_attempts() == 0

    def test_non_ascii_proof_is_400_not_500(
        self, api_client, seeded_enrollment_key,
    ):
        """FR-WK-8: a non-ASCII proof is a shape error, never reaching compare_digest."""
        body = enroll_body(TEST_HOSTNAME)
        body["proof"] = "é" * 64
        resp = _post(api_client, body)
        assert resp.status_code == 400
        assert set(resp.json()) == {"proof"}
        assert _limiter_attempts() == 0

    def test_not_json_returns_400(
        self, api_client, seeded_enrollment_key,
    ):
        """AC-11: a body that is not a JSON object is refused."""
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
    def test_five_wrong_proofs_then_429(
        self, api_client, seeded_enrollment_key,
    ):
        """AC-10: five failed proofs from one IP, then 429 before anything else."""
        for _ in range(5):
            resp = _post(api_client, enroll_body(TEST_HOSTNAME, key=OTHER_KEY))
            assert resp.status_code == 403
        resp = _post(api_client, enroll_body(TEST_HOSTNAME))
        assert resp.status_code == 429
        assert "Too many" in resp.json()["detail"]
