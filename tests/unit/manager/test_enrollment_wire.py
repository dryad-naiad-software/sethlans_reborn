# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Cross-boundary unit tests for the enroll wire format (setup-rebuild FR-WK-8).

The manager and the worker each carry their own ``enrollment_key`` module;
the pinned vectors below were computed once with plain ``hashlib``/``hmac``
(``blake2b(key, person=b"sethlans-proof-1"|b"sethlans-token-1",
digest_size=32)`` and ``HMAC-SHA256(proof_subkey, nonce + "\\n" + hostname)``),
so a change on either side fails here.  The round trip builds the answer
with the manager's ``build_envelope`` and opens it with the worker's
``enrollment_reply``.  Pure functions: no database, no network.
"""

import base64
import json
import re

import pytest
from sethlans_worker_agent import enrollment_key as worker_key
from sethlans_worker_agent import enrollment_reply

from workers import enrollment_key as manager_key
from workers.services.enrollment_envelope import build_envelope

KEY = "4F9XK2PBQ7M3N8RT"
NONCE = "00112233445566778899aabbccddeeff"
HOSTNAME = "worker-01"
PROOF_SUBKEY_HEX = "5ee81b6497c1191c843da33149a7ce099cdef68826bab3287a17b2adb60802b0"
TOKEN_SUBKEY_HEX = "ad47494483414c8ff82f27b06a894944554e5c678047f72213837f894c21a445"
PROOF_HEX = "346bd66a7b5c144f7984088cde15d87c3318e77641608ef71a157451e0224d7f"

TOKEN = "0123456789abcdef0123456789abcdef01234567"
MANAGER_ID = "00000000-0000-0000-0000-000000000042"
FINGERPRINT = "ab" * 32


@pytest.mark.parametrize("side", [manager_key, worker_key], ids=["manager", "worker"])
def test_proof_vectors_are_pinned_on_both_sides(side):
    """FR-WK-8: one key, nonce and hostname give the same subkeys and proof on manager and worker."""
    assert side.derive_proof_subkey(KEY).hex() == PROOF_SUBKEY_HEX
    assert side.derive_token_subkey(KEY).hex() == TOKEN_SUBKEY_HEX
    assert side.compute_proof(KEY, NONCE, HOSTNAME) == PROOF_HEX


def test_manager_reply_has_no_clear_token_and_the_worker_opens_it():
    """FR-WK-8: the payload carries api_token_nonce/api_token_ct only, and decrypts to the token."""
    envelope = build_envelope(KEY, TOKEN, NONCE, MANAGER_ID, FINGERPRINT)
    payload = envelope["payload"]
    assert set(payload) == {"api_token_ct", "api_token_nonce", "cert_fingerprint", "manager_id", "nonce"}
    assert TOKEN not in json.dumps(envelope)
    assert re.fullmatch(r"[0-9a-f]{24}", payload["api_token_nonce"])
    assert base64.b64encode(base64.b64decode(payload["api_token_ct"], validate=True)).decode() == payload["api_token_ct"]
    opened = enrollment_reply.open_reply(
        enrollment_reply.validate_envelope(envelope), KEY, NONCE, FINGERPRINT,
    )
    assert opened == {"api_token": TOKEN, "cert_fingerprint": FINGERPRINT, "manager_id": MANAGER_ID}
