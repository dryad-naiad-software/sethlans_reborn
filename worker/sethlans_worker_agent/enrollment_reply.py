# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Check and open the manager's signed enroll answer (setup-rebuild FR-WK-8).

Order, each step only after the one before it passed:
  1. Envelope shape: exactly ``{payload, signature}``, the payload exactly
     ``{api_token_ct, api_token_nonce, cert_fingerprint, manager_id, nonce}``,
     plus an optional signed ``status`` (worker approval, FR-WK-5).
  2. The envelope HMAC (``derive_signing_subkey`` over canonical JSON).
  3. The echoed nonce equals the one sent.
  4. The signed ``cert_fingerprint`` equals the SHA-256 fingerprint of the
     certificate seen on the enroll connection (relay check).
  5. AES-GCM decryption of the API token under ``derive_token_subkey`` with
     ``nonce + "\\n" + manager_id`` as associated data.

Any failure raises an :class:`~.enrollment_errors.EnrollmentError`
subclass, and the caller stores nothing. Nothing here logs or puts into an
error message the key, the token or its ciphertext.
"""

import base64
import hashlib
import hmac
import json
import re
from typing import Dict, Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from sethlans_worker_agent.enrollment_errors import (
    CertificateMismatchError,
    EnrollmentError,
    HmacMismatchError,
    MalformedResponseError,
    NonceMismatchError,
    TokenDecryptError,
)
from sethlans_worker_agent.enrollment_key import (
    derive_signing_subkey,
    derive_token_subkey,
)

SUPPORTED_PAYLOAD_KEYS = frozenset(
    {"api_token_ct", "api_token_nonce", "cert_fingerprint", "manager_id", "nonce"}
)
OPTIONAL_PAYLOAD_KEYS = frozenset({"status"})  # FR-WK-5: passed through when the manager sends it
SUPPORTED_ENVELOPE_KEYS = frozenset({"payload", "signature"})

CERT_MISMATCH_MESSAGE = (
    "certificate does not match the manager's signed fingerprint (possible relay)"
)
CERT_UNREADABLE_MESSAGE = (
    "The certificate the manager presented on the enroll connection could "
    "not be read, so it cannot be checked against the signed fingerprint."
)

_FINGERPRINT_RE = re.compile(r"[0-9a-f]{64}")   # manager's get_cert_fingerprint: lowercase hex SHA-256
_TOKEN_NONCE_RE = re.compile(r"[0-9a-f]{24}")   # 12-byte AES-GCM nonce
_TOKEN_RE = re.compile(r"[\x21-\x7e]{1,512}")   # visible ASCII: sent in an Authorization header
_STATUS_RE = re.compile(r"[a-z_]{1,32}")


def canonical_json(payload: dict) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def verify_hmac(payload: dict, signature: str, signing_key: bytes) -> bool:
    expected = hmac.new(
        signing_key, canonical_json(payload), hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def validate_envelope(envelope) -> dict:
    """Return ``envelope`` when its shape is right; else raise :class:`MalformedResponseError`."""
    if not isinstance(envelope, dict):
        raise MalformedResponseError("Response was not a JSON object.")
    if set(envelope.keys()) != SUPPORTED_ENVELOPE_KEYS:
        raise MalformedResponseError("Unexpected response fields.")
    payload = envelope["payload"]
    if not isinstance(payload, dict):
        raise MalformedResponseError("Payload must be an object.")
    if not SUPPORTED_PAYLOAD_KEYS <= set(payload.keys()) <= SUPPORTED_PAYLOAD_KEYS | OPTIONAL_PAYLOAD_KEYS:
        raise MalformedResponseError("Payload keys invalid.")
    signature = envelope["signature"]
    if not isinstance(signature, str) or not signature.isascii():
        raise MalformedResponseError("Signature must be an ASCII string.")
    return envelope


def _check_certificate(payload: dict, observed_fingerprint: Optional[str]) -> str:
    """Return the signed fingerprint when it is the one seen on the enroll connection."""
    signed = payload["cert_fingerprint"]
    if not isinstance(signed, str) or not _FINGERPRINT_RE.fullmatch(signed):
        raise MalformedResponseError("Signed certificate fingerprint has the wrong format.")
    if not observed_fingerprint:
        raise EnrollmentError(CERT_UNREADABLE_MESSAGE)
    if not hmac.compare_digest(signed, observed_fingerprint):
        raise CertificateMismatchError(CERT_MISMATCH_MESSAGE)
    return signed


def _token_parts(payload: dict):
    """``(nonce_bytes, ciphertext_bytes, manager_id)`` from the payload, shapes checked."""
    manager_id = payload["manager_id"]
    if not isinstance(manager_id, str) or not manager_id:
        raise MalformedResponseError("Manager id must be a non-empty string.")
    token_nonce = payload["api_token_nonce"]
    if not isinstance(token_nonce, str) or not _TOKEN_NONCE_RE.fullmatch(token_nonce):
        raise MalformedResponseError("Token nonce must be 24 lowercase hex characters.")
    ciphertext = payload["api_token_ct"]
    if not isinstance(ciphertext, str):
        raise MalformedResponseError("Token ciphertext must be a string.")
    try:
        ct_bytes = base64.b64decode(ciphertext, validate=True)
    except ValueError as e:  # binascii.Error, or non-ASCII text
        raise MalformedResponseError("Token ciphertext is not valid base64.") from e
    return bytes.fromhex(token_nonce), ct_bytes, manager_id


def _decrypt_token(payload: dict, normalized_key: str, nonce: str) -> str:
    token_nonce, ct_bytes, manager_id = _token_parts(payload)
    aad = (nonce + "\n" + manager_id).encode("utf-8")
    try:
        plain = AESGCM(derive_token_subkey(normalized_key)).decrypt(token_nonce, ct_bytes, aad)
    except InvalidTag as e:
        raise TokenDecryptError("The API token in the manager's answer could not be decrypted.") from e
    try:
        token = plain.decode("ascii")
    except UnicodeDecodeError as e:
        raise TokenDecryptError("The decrypted API token is not ASCII text.") from e
    if not _TOKEN_RE.fullmatch(token):
        raise TokenDecryptError("The decrypted API token has the wrong format.")
    return token


def _status(value) -> Optional[str]:
    """The signed ``status`` (FR-WK-5): ``None`` or a short lowercase word."""
    if value is None or (isinstance(value, str) and _STATUS_RE.fullmatch(value)):
        return value
    raise MalformedResponseError("Status must be null or a short lowercase word.")


def open_reply(
    envelope: dict, normalized_key: str, nonce: str, observed_fingerprint: Optional[str],
) -> Dict[str, str]:
    """Check a shape-validated envelope and return ``{api_token, cert_fingerprint, manager_id}``.

    With a ``status`` in the payload the answer carries it too (FR-WK-5).

    ``observed_fingerprint`` is the SHA-256 fingerprint of the certificate
    seen on the enroll connection (``None`` when it could not be read).
    """
    payload = envelope["payload"]
    if not verify_hmac(payload, envelope["signature"], derive_signing_subkey(normalized_key)):
        raise HmacMismatchError("HMAC verification failed.")
    if payload["nonce"] != nonce:
        raise NonceMismatchError("Response nonce did not match request.")
    fingerprint = _check_certificate(payload, observed_fingerprint)
    token = _decrypt_token(payload, normalized_key, nonce)
    result = {
        "api_token": token,
        "cert_fingerprint": fingerprint,
        "manager_id": payload["manager_id"],
    }
    if "status" in payload:
        result["status"] = _status(payload["status"])
    return result
