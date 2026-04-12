# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Smoke tests for the worker enrollment HTTP client.

Covers:
  * Happy path: valid HMAC signature verifies and the triple is
    returned.
  * HMAC mismatch: a tampered payload raises ``HmacMismatchError``.
  * Nonce mismatch: an echoed nonce that differs from the sent one
    raises ``NonceMismatchError``.
  * Pre-network validation: a user-typed key containing a Crockford
    excluded character raises ``InvalidKeyError`` BEFORE any HTTP
    call is made.
"""

import hashlib
import hmac
import json
from unittest.mock import MagicMock

import pytest

from sethlans_worker_agent import enrollment_client, enrollment_key


TEST_KEY = "4F9XK2PBQ7M3N8RT"
TEST_HOST = "test-worker"
MANAGER_URL = "https://manager.test:8080/api/"


def _sign_payload(payload: dict, normalized_key: str) -> str:
    subkey = enrollment_key.derive_signing_subkey(normalized_key)
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hmac.new(subkey, canonical, hashlib.sha256).hexdigest()


def _mock_session_ok(mocker, payload, signature):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "payload": payload,
        "signature": signature,
    }
    session = MagicMock()
    session.post.return_value = response
    mocker.patch.object(enrollment_client, "get_session", return_value=session)
    return session, response


def _capture_request_nonce(mocker):
    """Mock get_session so we can later inspect the nonce sent."""
    captured = {}

    def post_side_effect(url, json=None, **kwargs):
        captured["nonce"] = json["nonce"]
        captured["body"] = json
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "payload": {
                "api_token": "tok",
                "cert_fingerprint": "f" * 64,
                "manager_id": "00000000-0000-0000-0000-000000000000",
                "nonce": json["nonce"],
            },
            "signature": "",
        }
        # Precompute a correct signature AFTER we know the nonce.
        payload = response.json.return_value["payload"]
        response.json.return_value["signature"] = _sign_payload(
            payload, TEST_KEY,
        )
        return response

    session = MagicMock()
    session.post.side_effect = post_side_effect
    mocker.patch.object(enrollment_client, "get_session", return_value=session)
    return captured, session


class TestHappyPath:
    def test_returns_triple_on_valid_response(self, mocker):
        captured, session = _capture_request_nonce(mocker)
        result = enrollment_client.enroll(MANAGER_URL, TEST_KEY, TEST_HOST)
        assert set(result.keys()) == {
            "api_token", "cert_fingerprint", "manager_id",
        }
        assert result["api_token"] == "tok"
        assert result["cert_fingerprint"] == "f" * 64
        # The hostname was passed through unchanged.
        assert captured["body"]["hostname"] == TEST_HOST
        # The sent enrollment_key is the normalized form.
        assert captured["body"]["enrollment_key"] == TEST_KEY

    def test_accepts_hyphenated_key(self, mocker):
        _capture_request_nonce(mocker)
        result = enrollment_client.enroll(
            MANAGER_URL, "4F9X-K2PB-Q7M3-N8RT", TEST_HOST,
        )
        assert result["api_token"] == "tok"


class TestNormalizationPreNetwork:
    def test_invalid_key_raises_without_network_call(self, mocker):
        bad_session = MagicMock()
        mocker.patch.object(
            enrollment_client, "get_session", return_value=bad_session,
        )
        with pytest.raises(enrollment_client.InvalidKeyError):
            enrollment_client.enroll(
                MANAGER_URL, "4F9X-K2PB-Q7M3-N8RI", TEST_HOST,  # trailing I
            )
        bad_session.post.assert_not_called()

    def test_too_short_key_raises_without_network_call(self, mocker):
        bad_session = MagicMock()
        mocker.patch.object(
            enrollment_client, "get_session", return_value=bad_session,
        )
        with pytest.raises(enrollment_client.InvalidKeyError):
            enrollment_client.enroll(MANAGER_URL, "4F9X-K2PB", TEST_HOST)
        bad_session.post.assert_not_called()


class TestHmacMismatch:
    def test_tampered_payload_raises(self, mocker):
        """Manager signed payload A, but returned payload B — the
        HMAC will not verify against the worker's recomputed digest."""
        nonce_holder = {}

        def post_side_effect(url, json=None, **kwargs):
            nonce_holder["nonce"] = json["nonce"]
            # Build a "good" payload that gets signed correctly...
            payload = {
                "api_token": "real-token",
                "cert_fingerprint": "a" * 64,
                "manager_id": "11111111-1111-1111-1111-111111111111",
                "nonce": json["nonce"],
            }
            signature = _sign_payload(payload, TEST_KEY)
            # ...then tamper with the api_token AFTER signing.
            payload["api_token"] = "attacker-token"
            response = MagicMock()
            response.status_code = 200
            response.json.return_value = {
                "payload": payload,
                "signature": signature,
            }
            return response

        session = MagicMock()
        session.post.side_effect = post_side_effect
        mocker.patch.object(
            enrollment_client, "get_session", return_value=session,
        )

        with pytest.raises(enrollment_client.HmacMismatchError):
            enrollment_client.enroll(MANAGER_URL, TEST_KEY, TEST_HOST)


class TestNonceMismatch:
    def test_echoed_wrong_nonce_raises(self, mocker):
        def post_side_effect(url, json=None, **kwargs):
            payload = {
                "api_token": "tok",
                "cert_fingerprint": "a" * 64,
                "manager_id": "11111111-1111-1111-1111-111111111111",
                "nonce": "definitely-not-the-sent-nonce-12345",
            }
            signature = _sign_payload(payload, TEST_KEY)
            response = MagicMock()
            response.status_code = 200
            response.json.return_value = {
                "payload": payload, "signature": signature,
            }
            return response

        session = MagicMock()
        session.post.side_effect = post_side_effect
        mocker.patch.object(
            enrollment_client, "get_session", return_value=session,
        )

        with pytest.raises(enrollment_client.NonceMismatchError):
            enrollment_client.enroll(MANAGER_URL, TEST_KEY, TEST_HOST)


class TestMalformedEnvelope:
    def test_extra_top_level_field_rejected(self, mocker):
        payload = {
            "api_token": "tok",
            "cert_fingerprint": "a" * 64,
            "manager_id": "11111111-1111-1111-1111-111111111111",
            "nonce": "placeholder",
        }
        signature = _sign_payload(payload, TEST_KEY)
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "payload": payload,
            "signature": signature,
            "extra": "oops",
        }
        session = MagicMock()
        session.post.return_value = response
        mocker.patch.object(
            enrollment_client, "get_session", return_value=session,
        )
        with pytest.raises(enrollment_client.MalformedResponseError):
            enrollment_client.enroll(MANAGER_URL, TEST_KEY, TEST_HOST)

    def test_extra_payload_key_rejected(self, mocker):
        def post_side_effect(url, json=None, **kwargs):
            payload = {
                "api_token": "tok",
                "cert_fingerprint": "a" * 64,
                "manager_id": "id",
                "nonce": json["nonce"],
                "sneaky": "extra",
            }
            signature = _sign_payload(payload, TEST_KEY)
            response = MagicMock()
            response.status_code = 200
            response.json.return_value = {
                "payload": payload, "signature": signature,
            }
            return response

        session = MagicMock()
        session.post.side_effect = post_side_effect
        mocker.patch.object(
            enrollment_client, "get_session", return_value=session,
        )
        with pytest.raises(enrollment_client.MalformedResponseError):
            enrollment_client.enroll(MANAGER_URL, TEST_KEY, TEST_HOST)


class TestHttpErrorMapping:
    @pytest.mark.parametrize(
        "status,error_cls",
        [
            (403, enrollment_client.InvalidKeyError),
            (429, enrollment_client.RateLimitedError),
            (503, enrollment_client.ServiceUnavailableError),
        ],
    )
    def test_status_maps_to_error(self, mocker, status, error_cls):
        response = MagicMock()
        response.status_code = status
        response.text = "denied"
        response.json.return_value = {}
        session = MagicMock()
        session.post.return_value = response
        mocker.patch.object(
            enrollment_client, "get_session", return_value=session,
        )
        with pytest.raises(error_cls):
            enrollment_client.enroll(MANAGER_URL, TEST_KEY, TEST_HOST)

    def test_400_replayed_nonce_raises_nonce_mismatch(self, mocker):
        response = MagicMock()
        response.status_code = 400
        response.text = '{"detail": "Replayed nonce."}'
        response.json.return_value = {"detail": "Replayed nonce."}
        session = MagicMock()
        session.post.return_value = response
        mocker.patch.object(
            enrollment_client, "get_session", return_value=session,
        )
        with pytest.raises(enrollment_client.NonceMismatchError):
            enrollment_client.enroll(MANAGER_URL, TEST_KEY, TEST_HOST)
