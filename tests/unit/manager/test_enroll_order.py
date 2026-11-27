# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Regression tests for the FR-18 request-processing order, with the
setup-rebuild FR-WK-8 key proof.

The enroll view commits to this exact ordering:

    rate-limit → legacy-key 400 → shapes 400 → runtime_state 503 →
    key proof 403 → nonce check-and-record 400 → atomic DB → envelope

The invariants this file pins:

1. A wrong-proof attempt does NOT consume the nonce (AC-9a, FR-WK-8).
   The proof is checked BEFORE ``nonce_store.check_and_record``, so a
   bad proof rejects without touching the store, and the same nonce with
   a good proof still succeeds.

2. A replayed-nonce attempt does NOT increment the rate limiter
   (FR-13 / AC-10).

3. Readiness failure → 503 BEFORE any DB query (AC-43): the proof check
   is the first step that touches ``ManagerSettings``.

4. Shape checks (legacy key, field shapes) answer 400 before readiness.
"""

import pytest
from rest_framework.test import APIClient

from sethlans_manager import runtime_state
from tests.unit.manager._enroll_body import KEY, OTHER_KEY, enroll_body
from workers.models import ManagerSettings
from workers.views import enroll as enroll_module

TEST_HOSTNAME = "order-test-host"
ORM_GET = "workers.models.ManagerSettings.objects.get"


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture(autouse=True)
def _reset_enroll_module_state():
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
    prev_mid = runtime_state.manager_id
    prev_fp = runtime_state.cert_fingerprint
    runtime_state.manager_id = "00000000-0000-0000-0000-000000000042"
    runtime_state.cert_fingerprint = "ab" * 32
    yield
    runtime_state.manager_id = prev_mid
    runtime_state.cert_fingerprint = prev_fp


@pytest.fixture
def seeded_enrollment_key(db):
    row = ManagerSettings.objects.get(pk=1)
    row.enrollment_key = KEY
    row.save(
        update_fields=["enrollment_key", "enrollment_key_updated_at"],
    )
    return KEY


def _post(api_client, body):
    return api_client.post("/api/enroll/", body, format="json")


def _limiter_attempts():
    with enroll_module._rate_limiter._lock:
        return len(enroll_module._rate_limiter._attempts)


@pytest.mark.django_db
class TestAC9aWrongProofDoesNotConsumeNonce:
    def test_bad_proof_then_good_proof_same_nonce_succeeds(
        self, api_client, seeded_enrollment_key,
    ):
        """AC-9a / FR-WK-8: the proof is checked before the nonce is recorded."""
        bad = enroll_body(TEST_HOSTNAME, key=OTHER_KEY)
        assert _post(api_client, bad).status_code == 403
        with enroll_module._nonce_store._lock:
            assert bad["nonce"] not in enroll_module._nonce_store._seen
        good = enroll_body(TEST_HOSTNAME, nonce=bad["nonce"])
        assert _post(api_client, good).status_code == 200


@pytest.mark.django_db
class TestReplayedNonceSkipsRateLimiter:
    def test_replayed_nonce_does_not_increment_limiter(
        self, api_client, seeded_enrollment_key,
    ):
        """FR-13 / AC-10: only failed proofs count against the limit."""
        body = enroll_body(TEST_HOSTNAME)
        assert _post(api_client, body).status_code == 200
        second = _post(api_client, body)
        assert second.status_code == 400
        assert second.json() == {"detail": "Replayed nonce."}
        assert _limiter_attempts() == 0


@pytest.mark.django_db
class TestReadinessShortCircuitsBeforeDB:
    @pytest.mark.parametrize("missing", ["manager_id", "cert_fingerprint"])
    def test_not_ready_is_503_with_no_db_query_and_no_limit_count(
        self, api_client, seeded_enrollment_key, mocker, missing,
    ):
        """AC-14b / AC-43: 503 before the ORM is touched; the limiter is not charged."""
        setattr(runtime_state, missing, None)
        get_mock = mocker.patch(
            ORM_GET,
            side_effect=AssertionError(
                "ORM was queried before the readiness check",
            ),
        )
        resp = _post(api_client, enroll_body(TEST_HOSTNAME))
        assert resp.status_code == 503
        assert resp.json() == {"detail": "Enrollment service not ready."}
        get_mock.assert_not_called()
        assert _limiter_attempts() == 0


@pytest.mark.django_db
class TestShapeChecksBeforeReadiness:
    @pytest.mark.parametrize("body", [
        {"enrollment_key": KEY, "hostname": TEST_HOSTNAME, "nonce": "A" * 22},
        {"hostname": TEST_HOSTNAME, "nonce": "A" * 32, "proof": "0" * 64},
    ], ids=["legacy-key", "bad-nonce-shape"])
    def test_refused_body_is_400_even_when_not_ready(
        self, api_client, seeded_enrollment_key, body,
    ):
        """FR-WK-8 order: legacy-key and shape 400s come before the readiness 503."""
        runtime_state.manager_id = None
        assert _post(api_client, body).status_code == 400
