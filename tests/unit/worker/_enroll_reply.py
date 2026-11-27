# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Test helpers: a valid signed enroll answer in the setup-rebuild FR-WK-8 shape.

The answer is built here with the worker-side subkeys and ``AESGCM`` so the
worker tests need no manager code: the token is encrypted under
``derive_token_subkey`` with ``nonce + "\\n" + manager_id`` as associated data,
and the payload is signed under ``derive_signing_subkey``. ``PEER_DER`` is the
certificate a fake response says it saw; ``PEER_FP`` is its SHA-256.
"""

import base64
import hashlib
import hmac
import json
from unittest.mock import MagicMock

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from sethlans_worker_agent import enrollment_key

KEY = "4F9XK2PBQ7M3N8RT"
TOKEN = "0123456789abcdef0123456789abcdef01234567"   # DRF token shape
MANAGER_ID = "11111111-1111-1111-1111-111111111111"
TOKEN_NONCE = "00112233445566778899aabb"              # 12 bytes
PEER_DER = b"0\x82fake-der-certificate"
PEER_FP = hashlib.sha256(PEER_DER).hexdigest()


def encrypt(plain: bytes, nonce: str, key=KEY, manager_id=MANAGER_ID, token_nonce=TOKEN_NONCE) -> str:
    """Standard padded base64 of the AES-GCM output, as the manager sends it."""
    aad = (nonce + "\n" + manager_id).encode("utf-8")
    ct = AESGCM(enrollment_key.derive_token_subkey(key)).encrypt(bytes.fromhex(token_nonce), plain, aad)
    return base64.b64encode(ct).decode("ascii")


def sign(payload: dict, key=KEY) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hmac.new(enrollment_key.derive_signing_subkey(key), canonical, hashlib.sha256).hexdigest()


def payload_for(nonce: str, token: str = TOKEN, **overrides) -> dict:
    payload = {
        "api_token_ct": encrypt(token.encode("utf-8"), nonce),
        "api_token_nonce": TOKEN_NONCE,
        "cert_fingerprint": PEER_FP,
        "manager_id": MANAGER_ID,
        "nonce": nonce,
    }
    payload.update(overrides)
    return payload


def envelope_for(nonce: str, **overrides) -> dict:
    """A correctly signed envelope; ``overrides`` replace payload fields before signing."""
    payload = payload_for(nonce, **overrides)
    return {"payload": payload, "signature": sign(payload)}


def response(envelope, der=PEER_DER, status=200):
    """A fake ``requests.Response`` carrying ``envelope`` and, unless ``der`` is None, a peer certificate."""
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = envelope
    resp.peer_cert_der = der
    return resp
