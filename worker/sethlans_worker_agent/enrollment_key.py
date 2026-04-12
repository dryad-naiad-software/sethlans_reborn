# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Worker-side copy of the enrollment key helpers.

This module is a deliberate copy of the manager's ``enrollment_key``
module (normalize + derive_signing_subkey). The manager/worker boundary
forbids code sharing, so both sides carry the same rules verbatim. Any
drift between the two copies is caught by the cross-boundary parity
test (AC-44). The worker copy deliberately omits ``generate_key``,
``load_current``, and ``rotate`` — those are manager-only operations.
"""

import hashlib
import re

# Crockford base32: omits I, L, O, U from the standard base32 alphabet.
CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # 32 chars
KEY_LENGTH = 16
CANONICAL_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{16}$")

# BLAKE2b personalization bytes. MUST be exactly 16 bytes to match the
# manager's derivation. The spec's v2 source literally says
# b"sethlans-enroll-v1" (18 bytes), which raises ValueError from
# hashlib; the agreed workaround is to truncate to the first 16 bytes
# so both sides derive byte-identical subkeys. See the test vector in
# tests/unit/worker/test_enrollment_key.py and the matching manager copy.
HMAC_PERSONALIZATION = b"sethlans-enroll-"  # exactly 16 bytes


def normalize(key: str) -> str:
    """Return the canonical 16-char form of an enrollment key.

    Rules (identical on manager and worker):
      1. Strip leading/trailing whitespace.
      2. Uppercase.
      3. Remove hyphens.
      4. Reject any I/L/O/U (Crockford's excluded characters) with
         ``ValueError``.
      5. Final string must match ``^[0-9A-HJKMNP-TV-Z]{16}$``.
    """
    if not isinstance(key, str):
        raise ValueError("Enrollment key must be a string")
    s = key.strip().upper().replace("-", "")
    for ch in ("I", "L", "O", "U"):
        if ch in s:
            raise ValueError(
                "Enrollment key contains ambiguous character: "
                "I/L/O/U not allowed"
            )
    if not CANONICAL_RE.match(s):
        raise ValueError("Enrollment key format invalid")
    return s


def derive_signing_subkey(normalized_key: str) -> bytes:
    """Derive a 32-byte HMAC signing subkey via BLAKE2b.

    Must be called with the output of :func:`normalize`. Personalization
    pins the derivation to this protocol version.
    """
    return hashlib.blake2b(
        normalized_key.encode("utf-8"),
        person=HMAC_PERSONALIZATION,
        digest_size=32,
    ).digest()
