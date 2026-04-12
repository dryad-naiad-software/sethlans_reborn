# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Regression tests for the FR-18 request-processing order.

The enroll view commits to this exact ordering:

    rate-limit → serializer → runtime_state → key validation →
    nonce check-and-record → atomic DB → sign → return

The invariants this file pins:

1. An invalid-key attempt does NOT consume the nonce (AC-9a).
   Reason: key validation runs BEFORE ``nonce_store.check_and_record``,
   so a bad key rejects without touching the store.  A subsequent
   request with the same nonce + valid key still succeeds.

2. A replayed-nonce attempt does NOT increment the rate limiter
   (per FR-13 / AC-10).  Reason: the replay check happens after key
   validation — if the key is valid on the second call, no invalid-
   key branch runs, and the limiter stays untouched.

3. ``runtime_state.manager_id is None`` → 503 BEFORE any DB query.
   Reason: the readiness check runs before key validation, which is
   the first thing that touches ``ManagerSettings``.  If readiness
   fails, the view must not hit the ORM at all.
"""

import base64
import secrets

import pytest
from rest_framework.test import APIClient

from sethlans_manager import runtime_state
from workers.models import ManagerSettings
from workers.views import enroll as enroll_module


TEST_KEY_CANONICAL = "4F9XK2PBQ7M3N8RT"
TEST_HOSTNAME = "order-test-host"


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
    row.enrollment_key = TEST_KEY_CANONICAL
    row.save(
        update_fields=["enrollment_key", "enrollment_key_updated_at"],
    )
    return TEST_KEY_CANONICAL


def _nonce() -> str:
    return base64.urlsafe_b64encode(
        secrets.token_bytes(16),
    ).rstrip(b"=").decode("ascii")


def _post(api_client, enrollment_key, nonce, hostname=TEST_HOSTNAME):
    return api_client.post(
        "/api/enroll/",
        {
            "enrollment_key": enrollment_key,
            "hostname": hostname,
            "nonce": nonce,
        },
        format="json",
    )


# ---------------------------------------------------------------------------
# AC-9a regression: invalid-key attempt must NOT consume the nonce
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAC9aInvalidKeyDoesNotConsumeNonce:
    def test_bad_key_then_good_key_same_nonce_succeeds(
        self, api_client, seeded_enrollment_key,
    ):
        shared_nonce = _nonce()
        # 1st attempt: valid nonce, WRONG key → 403.
        bad = _post(api_client, "00000000000000PP", shared_nonce)
        assert bad.status_code == 403
        # Store must still be empty — nonce not consumed.
        with enroll_module._nonce_store._lock:
            assert shared_nonce not in enroll_module._nonce_store._seen
        # 2nd attempt: same nonce, correct key → 200.
        good = _post(api_client, TEST_KEY_CANONICAL, shared_nonce)
        assert good.status_code == 200


# ---------------------------------------------------------------------------
# Replayed-nonce attempt must NOT bump the rate limiter
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReplayedNonceSkipsRateLimiter:
    def test_replayed_nonce_does_not_increment_limiter(
        self, api_client, seeded_enrollment_key,
    ):
        nonce = _nonce()
        # 1st: valid key + fresh nonce → 200 (no limiter bump).
        first = _post(api_client, TEST_KEY_CANONICAL, nonce)
        assert first.status_code == 200
        with enroll_module._rate_limiter._lock:
            count_before = len(enroll_module._rate_limiter._attempts)
        # 2nd: same nonce + valid key → 400 replayed.
        second = _post(api_client, TEST_KEY_CANONICAL, nonce)
        assert second.status_code == 400
        assert second.json() == {"detail": "Replayed nonce."}
        # The rate-limiter attempts dict is unchanged.
        with enroll_module._rate_limiter._lock:
            count_after = len(enroll_module._rate_limiter._attempts)
        assert count_before == count_after


# ---------------------------------------------------------------------------
# readiness=None short-circuits BEFORE any ORM work (AC-43)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReadinessShortCircuitsBeforeDB:
    def test_no_db_query_when_manager_id_none(
        self, api_client, seeded_enrollment_key, mocker,
    ):
        """503 path must not touch ``ManagerSettings.objects.get``."""
        runtime_state.manager_id = None
        # Spy on the ORM — any call means the order contract is broken.
        get_mock = mocker.patch(
            "workers.models.ManagerSettings.objects.get",
            side_effect=AssertionError(
                "ORM was queried before the readiness check",
            ),
        )
        resp = _post(api_client, TEST_KEY_CANONICAL, _nonce())
        assert resp.status_code == 503
        get_mock.assert_not_called()

    def test_no_db_query_when_cert_fingerprint_none(
        self, api_client, seeded_enrollment_key, mocker,
    ):
        runtime_state.cert_fingerprint = None
        get_mock = mocker.patch(
            "workers.models.ManagerSettings.objects.get",
            side_effect=AssertionError(
                "ORM was queried before the readiness check",
            ),
        )
        resp = _post(api_client, TEST_KEY_CANONICAL, _nonce())
        assert resp.status_code == 503
        get_mock.assert_not_called()
