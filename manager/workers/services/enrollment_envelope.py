# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The signed answer of ``POST /api/enroll/`` (setup-rebuild FR-WK-8).

The API token never travels in clear.  It is encrypted with AES-GCM
(``cryptography``'s ``AESGCM``) under ``derive_token_subkey(key)`` with a
fresh 12-byte nonce; the associated data is ``nonce + "\\n" + manager_id``
so the ciphertext only opens for this request and this manager.  The
payload is then signed with HMAC-SHA256 under ``derive_signing_subkey``
over its canonical JSON, so the encrypted fields are covered too.

Pure functions: no logging, so the token and ciphertext never reach a log.
"""

import base64
import hashlib
import hmac
import json
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..enrollment_key import derive_signing_subkey, derive_token_subkey

# AES-GCM nonce length in bytes (24 lowercase hex characters on the wire).
TOKEN_NONCE_BYTES = 12


def canonical_json(payload: dict) -> bytes:
    """Return the canonical UTF-8 JSON bytes that the signature covers.

    ``ensure_ascii=True`` is explicit so the canonical form is byte-
    identical across Python minor versions (the default is ``True``,
    but pinning it in code makes the invariant testable).
    """
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def sign_payload(payload: dict, signing_key: bytes) -> str:
    """Return the hex HMAC-SHA256 over the canonical payload JSON."""
    return hmac.new(
        signing_key, canonical_json(payload), hashlib.sha256,
    ).hexdigest()


def encrypt_token(
    normalized_key: str, api_token: str, nonce: str, manager_id: str,
) -> tuple[str, str]:
    """Encrypt *api_token* for the worker that sent *nonce*.

    Returns ``(api_token_nonce, api_token_ct)``: the AES-GCM nonce as 24
    lowercase hex characters and the ciphertext (with its tag) as
    standard base64 with padding.
    """
    token_nonce = secrets.token_bytes(TOKEN_NONCE_BYTES)
    aad = (nonce + "\n" + manager_id).encode("utf-8")
    ciphertext = AESGCM(derive_token_subkey(normalized_key)).encrypt(
        token_nonce, api_token.encode("utf-8"), aad,
    )
    return token_nonce.hex(), base64.b64encode(ciphertext).decode("ascii")


def build_envelope(
    normalized_key: str,
    api_token: str,
    nonce: str,
    manager_id: str,
    cert_fingerprint: str,
) -> dict:
    """Return the signed ``{"payload", "signature"}`` enroll answer."""
    token_nonce, token_ct = encrypt_token(
        normalized_key, api_token, nonce, manager_id,
    )
    payload = {
        "api_token_ct": token_ct,
        "api_token_nonce": token_nonce,
        "cert_fingerprint": cert_fingerprint,
        "manager_id": manager_id,
        "nonce": nonce,
    }
    signature = sign_payload(payload, derive_signing_subkey(normalized_key))
    return {"payload": payload, "signature": signature}
