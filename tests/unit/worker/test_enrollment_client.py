# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for the worker enrollment HTTP client (``enrollment_client.enroll``).

The session is faked at ``enrollment_client.unpinned_session``; the fake
manager answers with a valid FR-WK-8 envelope for the nonce it was sent
(``_enroll_reply``). Opening the answer's crypto in detail is in
``test_enrollment_reply.py``; this file checks what ``enroll`` sends, which
checks it wires in (signature, nonce, certificate of the connection) and the
pre-network refusals.
"""

import re
from unittest.mock import MagicMock

import pytest
import requests

from sethlans_worker_agent import enrollment_client, enrollment_key, enrollment_reply, tls_adapter
from tests.unit.worker import _enroll_reply as reply

TEST_HOST = "test-worker"
MANAGER_URL = "https://manager.test:8080/api/"


def _patch_session(mocker, session):
    """``enrollment_client.unpinned_session`` returns ``session``, used as a context manager."""
    session.__enter__.return_value = session
    return mocker.patch.object(enrollment_client, "unpinned_session", return_value=session)


def _answering(mocker, answer):
    """A fake session whose POST answers ``answer(body)``; returns the session."""
    session = MagicMock()
    session.post.side_effect = lambda url, json=None, **kwargs: answer(json)
    _patch_session(mocker, session)
    return session


def _valid(body, der=reply.PEER_DER):
    return reply.response(reply.envelope_for(body["nonce"]), der=der)


class TestRequestAndHappyPath:
    @pytest.mark.parametrize("typed_key", [reply.KEY, "4f9x-k2pb-q7m3-n8rt"])
    def test_body_holds_no_key_and_the_token_is_returned(self, mocker, typed_key):
        """FR-WK-8: the body is exactly hostname, 32-hex nonce and proof; the decrypted token comes back."""
        session = _answering(mocker, _valid)
        result = enrollment_client.enroll(MANAGER_URL, typed_key, TEST_HOST)
        assert result == {
            "api_token": reply.TOKEN, "cert_fingerprint": reply.PEER_FP, "manager_id": reply.MANAGER_ID,
        }
        url, body = session.post.call_args.args[0], session.post.call_args.kwargs["json"]
        assert url == MANAGER_URL + "enroll/"
        assert set(body) == {"hostname", "nonce", "proof"}
        assert re.fullmatch(r"[0-9a-f]{32}", body["nonce"])
        assert body["hostname"] == TEST_HOST
        assert body["proof"] == enrollment_key.compute_proof(reply.KEY, body["nonce"], TEST_HOST)
        assert reply.KEY not in repr(body)


class TestPreNetworkRefusals:
    @pytest.mark.parametrize("bad_key", ["4F9X-K2PB-Q7M3-N8RI", "4F9X-K2PB"])
    def test_invalid_key_raises_without_network_call(self, mocker, bad_key):
        """FR-21: a key that does not normalize is refused before any request."""
        session = _answering(mocker, _valid)
        with pytest.raises(enrollment_client.InvalidKeyError):
            enrollment_client.enroll(MANAGER_URL, bad_key, TEST_HOST)
        session.post.assert_not_called()

    @pytest.mark.parametrize("bad_host", ["", "host name", "höst", "a" * 254, None])
    def test_hostname_the_manager_refuses_stops_before_network(self, mocker, bad_host):
        """FR-WK-8: a hostname outside [A-Za-z0-9._-]{1,253} is refused before any request."""
        session = _answering(mocker, _valid)
        with pytest.raises(enrollment_client.InvalidHostnameError, match="Rename the computer"):
            enrollment_client.enroll(MANAGER_URL, reply.KEY, bad_host)
        session.post.assert_not_called()


class TestAnswerChecks:
    def test_tampered_payload_raises_hmac_mismatch(self, mocker):
        """FR-21: a payload changed after signing fails the signature check."""
        def answer(body):
            envelope = reply.envelope_for(body["nonce"])
            envelope["payload"]["manager_id"] = "22222222-2222-2222-2222-222222222222"
            return reply.response(envelope)

        _answering(mocker, answer)
        with pytest.raises(enrollment_client.HmacMismatchError):
            enrollment_client.enroll(MANAGER_URL, reply.KEY, TEST_HOST)

    def test_echoed_wrong_nonce_raises(self, mocker):
        """FR-21: a signed answer for another nonce is refused."""
        _answering(mocker, lambda body: reply.response(reply.envelope_for("f" * 32)))
        with pytest.raises(enrollment_client.NonceMismatchError):
            enrollment_client.enroll(MANAGER_URL, reply.KEY, TEST_HOST)

    @pytest.mark.parametrize("der, error, message", [
        (b"0\x82another-certificate", enrollment_client.CertificateMismatchError,
         re.escape(enrollment_reply.CERT_MISMATCH_MESSAGE)),
        (None, enrollment_client.EnrollmentError, "could not be read"),
    ], ids=["other-certificate", "no-certificate"])
    def test_certificate_of_the_connection_must_be_the_signed_one(self, mocker, der, error, message):
        """FR-WK-8 certificate check: a relay's certificate, or none, fails with nothing returned."""
        _answering(mocker, lambda body: _valid(body, der=der))
        with pytest.raises(error, match=message):
            enrollment_client.enroll(MANAGER_URL, reply.KEY, TEST_HOST)

    @pytest.mark.parametrize("where", ["envelope", "payload"])
    def test_extra_field_rejected(self, mocker, where):
        """FR-21: an extra envelope or payload field is a malformed answer."""
        def answer(body):
            envelope = reply.envelope_for(body["nonce"])
            target = envelope if where == "envelope" else envelope["payload"]
            target["sneaky"] = "extra"
            return reply.response(envelope)

        _answering(mocker, answer)
        with pytest.raises(enrollment_client.MalformedResponseError):
            enrollment_client.enroll(MANAGER_URL, reply.KEY, TEST_HOST)


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
        _patch_session(mocker, session)
        with pytest.raises(error_cls):
            enrollment_client.enroll(MANAGER_URL, reply.KEY, TEST_HOST)

    @pytest.mark.parametrize("status, detail, prefix", [
        (400, True, "Manager rejected the request: "),
        (400, False, "Manager rejected the request: "),
        (500, False, "Manager internal error (HTTP 500): "),
        (418, False, "Unexpected HTTP 418: "),
    ], ids=["400-detail", "400-text", "5xx", "other"])
    def test_server_text_in_the_message_is_printable_and_cut_to_200(self, status, detail, prefix):
        """FR-WK-1: manager text reaches CLI answers and logs, so control characters (CR/LF too) are removed and
        it is cut to 200 characters."""
        text = "bad\r\nline\x1b[2J\x00" + "x" * 300
        response = MagicMock()
        response.status_code = status
        response.text = text
        response.json.return_value = {"detail": text}
        response.json.side_effect = None if detail else ValueError("not json")
        error = enrollment_client._map_http_error(response)
        assert str(error) == prefix + ("badline[2J" + "x" * 300)[:enrollment_client.MAX_SERVER_TEXT]
        assert enrollment_client.MAX_SERVER_TEXT == 200

    def test_400_replayed_nonce_raises_nonce_mismatch(self, mocker):
        response = MagicMock()
        response.status_code = 400
        response.text = '{"detail": "Replayed nonce."}'
        response.json.return_value = {"detail": "Replayed nonce."}
        session = MagicMock()
        session.post.return_value = response
        _patch_session(mocker, session)
        with pytest.raises(enrollment_client.NonceMismatchError):
            enrollment_client.enroll(MANAGER_URL, reply.KEY, TEST_HOST)


class TestOwnSession:
    """#306: the POST goes through a new unpinned session, closed after the call, never the thread-local one."""

    def test_session_is_entered_and_exited_around_the_post(self, mocker):
        """#306; FR-WK-8: no redirects, so the answer comes from the connection the request went to."""
        session = _answering(mocker, _valid)
        enrollment_client.enroll(MANAGER_URL, reply.KEY, TEST_HOST)
        session.__enter__.assert_called_once_with()
        session.__exit__.assert_called_once()
        kwargs = session.post.call_args.kwargs
        assert (kwargs["timeout"], kwargs["verify"], kwargs["allow_redirects"]) == (15, False, False)

    def test_seam_is_the_tls_adapter_unpinned_session(self):
        assert enrollment_client.unpinned_session is tls_adapter.unpinned_session
        assert not hasattr(enrollment_client, "get_session")

    def test_network_error_is_an_enrollment_error(self, mocker):
        session = MagicMock()
        session.post.side_effect = requests.exceptions.ConnectionError("refused")
        _patch_session(mocker, session)
        with pytest.raises(enrollment_client.EnrollmentError, match="Network error"):
            enrollment_client.enroll(MANAGER_URL, reply.KEY, TEST_HOST)
        session.__exit__.assert_called_once()
