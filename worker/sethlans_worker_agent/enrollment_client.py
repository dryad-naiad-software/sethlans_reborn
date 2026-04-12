# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Worker-side enrollment HTTP client (FR-20, FR-21, FR-22).

Performs the single-shot POST to ``/api/enroll/`` and validates the
HMAC-signed response envelope. On success returns the
``{api_token, cert_fingerprint, manager_id}`` triple. On failure
raises an :class:`EnrollmentError` subclass that the wizard can
render to the user.

Key rules:
  * ``normalize()`` is called exactly ONCE at the top of ``enroll()``.
    The normalized string is the only form that flows through the
    rest of the function: sent on the wire, fed into ``blake2b`` for
    subkey derivation, and held in memory. The raw user-typed input
    is never used downstream (FR-21).
  * The request uses ``verify=False`` because no pinned fingerprint
    exists yet; ``InsecureRequestWarning`` is suppressed only for
    this call (FR-22).
  * Every response field is validated before the HMAC is verified
    (7 checks per FR-21). Only after all checks pass does the
    function return a payload.
"""

import base64
import hashlib
import hmac
import json
import logging
import secrets
import warnings
from typing import Dict

import requests
from urllib3.exceptions import InsecureRequestWarning

from sethlans_worker_agent.enrollment_key import (
    derive_signing_subkey,
    normalize,
)
from sethlans_worker_agent.tls_adapter import get_session

logger = logging.getLogger(__name__)

SUPPORTED_PAYLOAD_KEYS = frozenset(
    {"api_token", "cert_fingerprint", "manager_id", "nonce"}
)
SUPPORTED_ENVELOPE_KEYS = frozenset({"payload", "signature"})


class EnrollmentError(Exception):
    """Base class for all enrollment client failures."""


class InvalidKeyError(EnrollmentError):
    """Raised on HTTP 403, on malformed input keys, or on user error."""


class HmacMismatchError(EnrollmentError):
    """Raised when the response signature does not verify."""


class NonceMismatchError(EnrollmentError):
    """Raised when the response nonce does not match the request nonce."""


class MalformedResponseError(EnrollmentError):
    """Raised on unexpected response shape or missing/extra fields."""


class RateLimitedError(EnrollmentError):
    """Raised on HTTP 429."""


class ServiceUnavailableError(EnrollmentError):
    """Raised on HTTP 503 (manager enrollment service not ready)."""


def _generate_nonce() -> str:
    """Return a 22-char urlsafe base64 encoding of 16 random bytes."""
    return (
        base64.urlsafe_b64encode(secrets.token_bytes(16))
        .rstrip(b"=")
        .decode("ascii")
    )


def _canonical_json(payload: dict) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _verify_hmac(
    payload: dict, signature: str, signing_key: bytes,
) -> bool:
    expected = hmac.new(
        signing_key, _canonical_json(payload), hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def _map_http_error(resp: requests.Response) -> EnrollmentError:
    code = resp.status_code
    if code == 403:
        return InvalidKeyError("Enrollment key rejected by manager.")
    if code == 429:
        return RateLimitedError(
            "Manager rate-limited the enrollment attempt."
        )
    if code == 503:
        return ServiceUnavailableError(
            "Manager enrollment service not ready."
        )
    if code == 400:
        try:
            body = resp.json()
        except (ValueError, TypeError):
            body = None
        detail = body.get("detail") if isinstance(body, dict) else None
        if detail == "Replayed nonce.":
            return NonceMismatchError("Replayed nonce rejected by manager.")
        return MalformedResponseError(
            f"Manager rejected the request: {detail or resp.text}"
        )
    if code >= 500:
        return EnrollmentError(
            f"Manager internal error (HTTP {code}): {resp.text}"
        )
    return EnrollmentError(f"Unexpected HTTP {code}: {resp.text}")


def _validate_envelope(envelope) -> dict:
    if not isinstance(envelope, dict):
        raise MalformedResponseError("Response was not a JSON object.")
    if set(envelope.keys()) != SUPPORTED_ENVELOPE_KEYS:
        raise MalformedResponseError("Unexpected response fields.")
    payload = envelope["payload"]
    if not isinstance(payload, dict):
        raise MalformedResponseError("Payload must be an object.")
    if set(payload.keys()) != SUPPORTED_PAYLOAD_KEYS:
        raise MalformedResponseError("Payload keys invalid.")
    signature = envelope["signature"]
    if not isinstance(signature, str):
        raise MalformedResponseError("Signature must be a string.")
    return envelope


def _post_enroll(url: str, body: dict) -> requests.Response:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", InsecureRequestWarning)
            return get_session().post(
                url, json=body, timeout=15, verify=False,
            )
    except requests.exceptions.RequestException as e:
        raise EnrollmentError(f"Network error: {e}") from e


def _parse_envelope(resp: requests.Response) -> dict:
    if resp.status_code != 200:
        raise _map_http_error(resp)
    try:
        envelope = resp.json()
    except (ValueError, TypeError) as e:
        raise MalformedResponseError("Response was not JSON.") from e
    return _validate_envelope(envelope)


def enroll(
    manager_url: str, enrollment_key: str, hostname: str,
) -> Dict[str, str]:
    """Perform enrollment. Returns ``{api_token, cert_fingerprint, manager_id}``.

    Raises :class:`EnrollmentError` subclasses on failure. The
    ``enrollment_key`` argument may be hyphenated or compact —
    :func:`normalize` is called once at the top of this function and
    the normalized form is the only value that flows through the rest
    of the enrollment path.
    """
    try:
        normalized_key = normalize(enrollment_key)
    except ValueError as e:
        raise InvalidKeyError(str(e)) from e

    nonce = _generate_nonce()
    if not manager_url.endswith("/"):
        manager_url = manager_url + "/"
    url = f"{manager_url}enroll/"
    body = {
        "enrollment_key": normalized_key,
        "hostname": hostname,
        "nonce": nonce,
    }

    resp = _post_enroll(url, body)
    envelope = _parse_envelope(resp)
    payload = envelope["payload"]
    signature = envelope["signature"]

    if payload["nonce"] != nonce:
        raise NonceMismatchError("Response nonce did not match request.")

    signing_key = derive_signing_subkey(normalized_key)
    if not _verify_hmac(payload, signature, signing_key):
        raise HmacMismatchError("HMAC verification failed.")

    return {
        "api_token": payload["api_token"],
        "cert_fingerprint": payload["cert_fingerprint"],
        "manager_id": payload["manager_id"],
    }
