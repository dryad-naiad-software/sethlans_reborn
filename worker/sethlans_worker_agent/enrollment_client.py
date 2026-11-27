# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Worker-side enrollment HTTP client (FR-20, FR-21, FR-22; setup-rebuild FR-WK-8).

Performs the single-shot POST to ``/api/enroll/`` and opens the
HMAC-signed response envelope. On success returns the
``{api_token, cert_fingerprint, manager_id}`` triple. On failure
raises an :class:`EnrollmentError` subclass that the caller can
show to the user.

Key rules:
  * ``normalize()`` is called exactly ONCE at the top of ``enroll()``.
    The normalized string is the only form that flows through the
    rest of the function: fed into ``blake2b`` for subkey derivation
    and held in memory. The raw user-typed input is never used
    downstream (FR-21).
  * The key never leaves the worker (FR-WK-8). The request body is
    ``{"hostname", "nonce", "proof"}`` with ``proof`` an HMAC-SHA256 under
    ``derive_proof_subkey`` over ``nonce + "\\n" + hostname``. The nonce is
    32 lowercase hex characters (16 random bytes); the hostname must fit
    ``[A-Za-z0-9._-]{1,253}`` or enrollment stops before any network call.
  * The answer carries the API token AES-GCM encrypted under
    ``derive_token_subkey``; :mod:`.enrollment_reply` checks the signature,
    the nonce, the certificate and then decrypts. Nothing is returned (and
    so nothing is stored) unless every check passes.
  * Certificate check: the SHA-256 fingerprint of the certificate the
    manager presented on the enroll connection itself (captured by
    :class:`~.peer_cert.PeerCertAdapter`, mounted by ``unpinned_session``)
    must equal the signed ``cert_fingerprint``.
  * The request uses ``verify=False`` because no pinned fingerprint
    exists yet (FR-22). It goes through its own new session
    (``tls_adapter.unpinned_session``), never the thread-local one,
    which may be pinned to another manager. urllib3's
    ``InsecureRequestWarning`` for it is accurate and is not
    suppressed: ``warnings.catch_warnings`` changes process-wide state
    and would also hide other threads' warnings (#306).
  * Redirects are not followed: the answer must come from the connection
    the request went to.
  * Text from the manager's answer that goes into an error message is cut
    to ``MAX_SERVER_TEXT`` (200) characters with control and other
    non-printable characters removed (``_server_text``).
  * Nothing here logs the key, the proof, the token or its ciphertext.
"""

import logging
import re
import secrets
from typing import Dict

import requests

from sethlans_worker_agent.enrollment_errors import (
    CertificateMismatchError,
    EnrollmentError,
    HmacMismatchError,
    InvalidHostnameError,
    InvalidKeyError,
    MalformedResponseError,
    NonceMismatchError,
    RateLimitedError,
    ServiceUnavailableError,
    TokenDecryptError,
)
from sethlans_worker_agent.enrollment_key import compute_proof, normalize
from sethlans_worker_agent.enrollment_reply import open_reply, validate_envelope
from sethlans_worker_agent.peer_cert import peer_cert_fingerprint
from sethlans_worker_agent.tls_adapter import unpinned_session

logger = logging.getLogger(__name__)

# The error classes live in enrollment_errors; callers catch them here.
__all__ = [
    "CertificateMismatchError",
    "EnrollmentError",
    "HmacMismatchError",
    "InvalidHostnameError",
    "InvalidKeyError",
    "MalformedResponseError",
    "NonceMismatchError",
    "RateLimitedError",
    "ServiceUnavailableError",
    "TokenDecryptError",
    "enroll",
]

HOSTNAME_RE = re.compile(r"[A-Za-z0-9._-]{1,253}")
MAX_SERVER_TEXT = 200


def _server_text(value) -> str:
    """Text from the manager for an error message: printable characters only, at most ``MAX_SERVER_TEXT``."""
    return "".join(ch for ch in str(value) if ch.isprintable())[:MAX_SERVER_TEXT]


def _generate_nonce() -> str:
    """Return 32 lowercase hex characters (16 random bytes)."""
    return secrets.token_hex(16)


def _check_hostname(hostname) -> str:
    """Return ``hostname`` when the manager accepts its shape; else raise :class:`InvalidHostnameError`."""
    if isinstance(hostname, str) and HOSTNAME_RE.fullmatch(hostname):
        return hostname
    raise InvalidHostnameError(
        f"This computer's name {hostname!r} cannot be used to enroll: it must be "
        "1-253 characters of letters, digits, '.', '-' and '_'. Rename the "
        "computer and try again."
    )


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
            f"Manager rejected the request: {_server_text(detail or resp.text)}"
        )
    if code >= 500:
        return EnrollmentError(
            f"Manager internal error (HTTP {code}): {_server_text(resp.text)}"
        )
    return EnrollmentError(f"Unexpected HTTP {code}: {_server_text(resp.text)}")


def _post_enroll(url: str, body: dict) -> requests.Response:
    """POST through a new unpinned session, closed after the call (#306).

    Not the thread-local session: that one may already be pinned to another
    manager. The response is read in full before the session closes; the
    certificate seen on its connection rides on the response
    (:mod:`.peer_cert`).
    """
    try:
        with unpinned_session() as session:
            return session.post(
                url, json=body, timeout=15, verify=False, allow_redirects=False,
            )
    except requests.exceptions.RequestException as e:
        raise EnrollmentError(f"Network error: {_server_text(e)}") from e


def _parse_envelope(resp: requests.Response) -> dict:
    if resp.status_code != 200:
        raise _map_http_error(resp)
    try:
        envelope = resp.json()
    except (ValueError, TypeError) as e:
        raise MalformedResponseError("Response was not JSON.") from e
    return validate_envelope(envelope)


def enroll(
    manager_url: str, enrollment_key: str, hostname: str,
) -> Dict[str, str]:
    """Perform enrollment. Returns ``{api_token, cert_fingerprint, manager_id}``.

    The answer also carries ``status`` when the manager's signed payload
    has one (FR-WK-5).

    Raises :class:`EnrollmentError` subclasses on failure. The
    ``enrollment_key`` argument may be hyphenated or compact —
    :func:`normalize` is called once at the top of this function and
    the normalized form is the only value that flows through the rest
    of the enrollment path. The key itself is never sent.
    """
    try:
        normalized_key = normalize(enrollment_key)
    except ValueError as e:
        raise InvalidKeyError(str(e)) from e
    hostname = _check_hostname(hostname)

    nonce = _generate_nonce()
    if not manager_url.endswith("/"):
        manager_url = manager_url + "/"
    url = f"{manager_url}enroll/"
    body = {
        "hostname": hostname,
        "nonce": nonce,
        "proof": compute_proof(normalized_key, nonce, hostname),
    }

    resp = _post_enroll(url, body)
    envelope = _parse_envelope(resp)
    return open_reply(
        envelope, normalized_key, nonce, peer_cert_fingerprint(resp),
    )
