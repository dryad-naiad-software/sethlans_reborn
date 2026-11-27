# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``enrollment_reply.open_reply`` (setup-rebuild FR-WK-8).

Every payload here is correctly signed (``_enroll_reply.sign``), so each case
reaches the step it is about: the shape of the token fields and the AES-GCM
decryption. Signature, nonce and certificate wiring are covered through
``enroll`` in ``test_enrollment_client.py``; the manager-built round trip is
in ``tests/unit/manager/test_enrollment_wire.py``.
"""

import base64

import pytest

from sethlans_worker_agent import enrollment_reply
from sethlans_worker_agent.enrollment_errors import MalformedResponseError, TokenDecryptError
from tests.unit.worker import _enroll_reply as reply

NONCE = "00112233445566778899aabbccddeeff"


def _open(**overrides):
    payload = reply.payload_for(NONCE, **overrides)
    envelope = {"payload": payload, "signature": reply.sign(payload)}
    return enrollment_reply.open_reply(envelope, reply.KEY, NONCE, reply.PEER_FP)


def _flipped_ciphertext():
    raw = bytearray(base64.b64decode(reply.encrypt(reply.TOKEN.encode(), NONCE)))
    raw[0] ^= 0x01
    return base64.b64encode(bytes(raw)).decode("ascii")


@pytest.mark.parametrize("overrides", [
    {"api_token_ct": _flipped_ciphertext()},
    {"manager_id": "22222222-2222-2222-2222-222222222222"},   # other associated data
    {"api_token_nonce": "ffeeddccbbaa998877665544"},
    {"api_token_ct": reply.encrypt("tök".encode("utf-8"), NONCE)},
    {"api_token_ct": reply.encrypt(b"", NONCE)},
], ids=["tampered-ciphertext", "wrong-aad", "wrong-token-nonce", "non-ascii-token", "empty-token"])
def test_token_that_does_not_decrypt_cleanly_fails_with_no_token(overrides):
    """FR-WK-8: tampering, another manager_id or token nonce, or a bad plaintext gives no token."""
    with pytest.raises(TokenDecryptError) as caught:
        _open(**overrides)
    assert reply.TOKEN not in str(caught.value)


@pytest.mark.parametrize("overrides", [
    {"cert_fingerprint": reply.PEER_FP.upper()},
    {"api_token_nonce": "00112233445566778899AABB"},
    {"api_token_ct": "not base64!"},
    {"manager_id": ""},
], ids=["fingerprint-uppercase", "token-nonce-uppercase", "ciphertext-not-base64", "empty-manager-id"])
def test_bad_token_field_shapes_are_malformed(overrides):
    """FR-WK-8 wire details: fingerprint 64 lowercase hex, token nonce 24 lowercase hex, padded base64."""
    with pytest.raises(MalformedResponseError):
        _open(**overrides)
