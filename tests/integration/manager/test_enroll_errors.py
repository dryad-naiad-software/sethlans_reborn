# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for ``POST /api/enroll/`` error paths and FR-18
ordering guarantees.

Covers:

  * Replayed nonce → 400 + rate limiter not incremented (FR-13).
  * ``test_enroll_nonce_consumed_on_token_failure`` — DB failure AFTER
    the nonce was recorded (FR-18 step 5 vs step 6).
  * ``test_enroll_rolls_back_on_token_failure`` — no Worker/User/Token
    rows survive a DB failure in the atomic block.
  * Rate limit exhaustion via the full URL stack (AC-10).
  * ``test_enroll_503_when_runtime_state_missing`` — readiness check
    short-circuits BEFORE the DB and BEFORE the rate limiter.

Unit tests at ``tests/unit/manager/test_enroll_view.py`` cover the same
rules at the isolated-function level; these tests re-exercise them
through the full URL router so middleware and URL patterns are in the
picture.
"""

import pytest
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from workers.models import Worker
from workers.views import enroll as enroll_module

from .conftest import (
    TEST_ENROLLMENT_KEY_CANONICAL,
    fresh_nonce,
)

ENROLL_URL = '/api/enroll/'

# A valid-shape but-wrong key (16 Crockford chars, not the seeded value).
BAD_BUT_VALID_SHAPE_KEY = "00000000000000PP"


def _body(hostname='err-host', nonce=None, key=None):
    return {
        'enrollment_key': key or TEST_ENROLLMENT_KEY_CANONICAL,
        'hostname': hostname,
        'nonce': nonce or fresh_nonce(),
    }


@pytest.mark.django_db
class TestReplayedNonceDoesNotIncrementRateLimiter:
    """FR-13: a replayed nonce returns 400 but does NOT count against rate-limit."""

    def test_replayed_nonce_returns_400_and_leaves_limiter_alone(
        self,
        seeded_enrollment_key,
        ready_runtime_state,
        reset_enroll_module_state,
    ):
        client = APIClient()
        nonce = fresh_nonce()

        first = client.post(
            ENROLL_URL, _body(hostname='replay-host', nonce=nonce),
            format='json',
        )
        assert first.status_code == 200

        # Snapshot the rate limiter state.
        with enroll_module._rate_limiter._lock:
            before = dict(enroll_module._rate_limiter._attempts)

        second = client.post(
            ENROLL_URL, _body(hostname='replay-host-2', nonce=nonce),
            format='json',
        )
        assert second.status_code == 400
        assert second.json() == {'detail': 'Replayed nonce.'}

        with enroll_module._rate_limiter._lock:
            after = dict(enroll_module._rate_limiter._attempts)
        assert before == after


@pytest.mark.django_db
class TestNonceConsumedOnTokenFailure:
    """FR-18 step 5 vs step 6: nonce recording precedes the atomic block.

    If ``Token.objects.get_or_create`` blows up mid-request, the nonce
    is still marked as seen — the worker MUST retry with a fresh nonce.
    """

    def test_enroll_nonce_consumed_on_token_failure(
        self,
        seeded_enrollment_key,
        ready_runtime_state,
        reset_enroll_module_state,
        mocker,
    ):
        """DB explosion in step 6 still burns the nonce from step 5."""
        client = APIClient()
        nonce = fresh_nonce()

        # Patch ``Token.objects.get_or_create`` to raise on the first call.
        mocker.patch(
            'workers.views.enroll.Token.objects.get_or_create',
            side_effect=RuntimeError('boom'),
        )

        first = client.post(
            ENROLL_URL, _body(hostname='burn-host', nonce=nonce),
            format='json',
        )
        assert first.status_code == 500
        assert first.json() == {'detail': 'Enrollment failed.'}

        # Retrying with the SAME nonce must now hit the replay branch —
        # the nonce was recorded before the atomic block opened.
        mocker.stopall()
        second = client.post(
            ENROLL_URL, _body(hostname='burn-host', nonce=nonce),
            format='json',
        )
        assert second.status_code == 400
        assert second.json() == {'detail': 'Replayed nonce.'}

    def test_enroll_rolls_back_on_token_failure(
        self,
        seeded_enrollment_key,
        ready_runtime_state,
        reset_enroll_module_state,
        mocker,
    ):
        """A failure in the atomic block leaves NO new Worker/User/Token rows."""
        client = APIClient()

        mocker.patch(
            'workers.views.enroll.Token.objects.get_or_create',
            side_effect=RuntimeError('boom'),
        )

        worker_before = Worker.objects.count()
        from django.contrib.auth import get_user_model
        User = get_user_model()
        user_before = User.objects.count()
        token_before = Token.objects.count()

        resp = client.post(
            ENROLL_URL, _body(hostname='rollback-host'),
            format='json',
        )
        assert resp.status_code == 500

        assert Worker.objects.count() == worker_before
        assert User.objects.count() == user_before
        assert Token.objects.count() == token_before


@pytest.mark.django_db
class TestRateLimitExhaustion:
    """AC-10 via the full URL router."""

    def test_five_invalid_attempts_then_429(
        self,
        seeded_enrollment_key,
        ready_runtime_state,
        reset_enroll_module_state,
    ):
        """5 invalid-key attempts from the same IP → 6th returns 429."""
        client = APIClient()
        for _ in range(5):
            resp = client.post(
                ENROLL_URL,
                _body(hostname='rate-host', key=BAD_BUT_VALID_SHAPE_KEY),
                format='json',
                REMOTE_ADDR='198.51.100.42',
            )
            assert resp.status_code == 403

        resp = client.post(
            ENROLL_URL,
            _body(hostname='rate-host', key=BAD_BUT_VALID_SHAPE_KEY),
            format='json',
            REMOTE_ADDR='198.51.100.42',
        )
        assert resp.status_code == 429
        assert 'Too many' in resp.json()['detail']

    def test_rate_limit_is_per_ip(
        self,
        seeded_enrollment_key,
        ready_runtime_state,
        reset_enroll_module_state,
    ):
        """Exhausting one IP does not cap a different IP."""
        client = APIClient()
        for _ in range(5):
            client.post(
                ENROLL_URL,
                _body(hostname='rate-host-a', key=BAD_BUT_VALID_SHAPE_KEY),
                format='json',
                REMOTE_ADDR='203.0.113.1',
            )
        # IP A is now capped.  IP B starts fresh — the next attempt from
        # B should return 403 (invalid key), not 429.
        resp = client.post(
            ENROLL_URL,
            _body(hostname='rate-host-b', key=BAD_BUT_VALID_SHAPE_KEY),
            format='json',
            REMOTE_ADDR='203.0.113.99',
        )
        assert resp.status_code == 403


@pytest.mark.django_db
class TestInvalidKeyPath:
    """Invalid-but-valid-shape key returns 403 and increments the limiter."""

    def test_wrong_key_returns_403_and_increments_limiter(
        self,
        seeded_enrollment_key,
        ready_runtime_state,
        reset_enroll_module_state,
    ):
        client = APIClient()
        resp = client.post(
            ENROLL_URL,
            _body(hostname='wrong-host', key=BAD_BUT_VALID_SHAPE_KEY),
            format='json',
        )
        assert resp.status_code == 403
        assert resp.json() == {'detail': 'Invalid enrollment key.'}
        with enroll_module._rate_limiter._lock:
            assert len(enroll_module._rate_limiter._attempts) == 1


@pytest.mark.django_db
class TestReadinessNotReady:
    """FR-18 step 3: 503 before DB, before rate-limiter increment, before nonce."""

    def test_enroll_503_when_runtime_state_missing(
        self,
        seeded_enrollment_key,
        ready_runtime_state,
        reset_enroll_module_state,
    ):
        """``manager_id = None`` → 503 and nothing else happens."""
        # Override the fixture: knock ``manager_id`` back to None.
        ready_runtime_state.manager_id = None

        client = APIClient()
        from django.contrib.auth import get_user_model
        User = get_user_model()
        worker_before = Worker.objects.count()
        user_before = User.objects.count()
        token_before = Token.objects.count()

        resp = client.post(
            ENROLL_URL, _body(hostname='not-ready-host'),
            format='json',
        )
        assert resp.status_code == 503
        assert resp.json() == {'detail': 'Enrollment service not ready.'}

        # No rows were created.
        assert Worker.objects.count() == worker_before
        assert User.objects.count() == user_before
        assert Token.objects.count() == token_before

        # Rate limiter budget not burned.
        with enroll_module._rate_limiter._lock:
            assert len(enroll_module._rate_limiter._attempts) == 0

        # Nonce store untouched.
        with enroll_module._nonce_store._lock:
            assert enroll_module._nonce_store._seen == {}

    def test_cert_fingerprint_none_also_returns_503(
        self,
        seeded_enrollment_key,
        ready_runtime_state,
        reset_enroll_module_state,
    ):
        """The second branch of the readiness check fires independently."""
        ready_runtime_state.cert_fingerprint = None
        client = APIClient()
        resp = client.post(
            ENROLL_URL, _body(hostname='no-fp-host'),
            format='json',
        )
        assert resp.status_code == 503
