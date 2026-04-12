# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Enrollment key generation, normalisation, signing-subkey derivation,
and rotation helpers.

The canonical form of an enrollment key is 16 Crockford base32 characters
(no hyphens, uppercase).  The display form is hyphenated in four groups
of four for human readability.  ``I``, ``L``, ``O`` and ``U`` are rejected
outright, not substituted — silent substitution would split the codepath
between manager and worker and produce HMAC failures on otherwise-valid
keys.

A worker-side copy of ``normalize()`` and ``derive_signing_subkey()`` lives
in ``worker/sethlans_worker_agent/enrollment_key.py``.  Both sides MUST
produce byte-identical output for the same input — the shared test vector
in ``tests/unit/manager/test_enrollment_key.py`` pins this.
"""

import hashlib
import re
import secrets
import threading

# Crockford base32 omits ``I``, ``L``, ``O`` and ``U`` from the standard
# base32 alphabet: the first three are visually ambiguous with ``1`` and
# ``0`` and the last is excluded to avoid accidentally spelling profanity.
CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # 32 chars
KEY_LENGTH = 16
CANONICAL_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{16}$")

# Personalisation bytes for the HMAC signing subkey.  Any future protocol
# change that affects the canonical form of the signed payload MUST bump
# the suffix so a mismatched pair fails HMAC verification.
#
# NOTE: ``hashlib.blake2b`` caps the personalisation parameter at 16
# bytes.  The spec originally wrote ``b"sethlans-enroll-v1"`` which is
# 18 bytes and raises ``ValueError`` when passed to ``blake2b(person=)``.
# The canonical form used by both manager and worker is the 16-byte
# literal below.  Both files MUST use the same literal — no slice, no
# indirection.  A shared test vector pins the value (AC-44).
HMAC_PERSONALIZATION = b"sethlans-enroll-"  # exactly 16 bytes

# Lock held only around ``rotate()``'s read-modify-write; pure reads via
# ``load_current()`` are not locked — SQLite row-level locking is
# sufficient for intra-process reads.
_rotate_lock = threading.Lock()


def generate_key() -> str:
    """Return a freshly-generated 16-char Crockford base32 key."""
    return "".join(
        secrets.choice(CROCKFORD_ALPHABET) for _ in range(KEY_LENGTH)
    )


def normalize(key: str) -> str:
    """Return the canonical 16-char form.

    Rules (identical on manager and worker):

    1. Strip whitespace.
    2. Uppercase.
    3. Remove hyphens.
    4. Reject any ``I``/``L``/``O``/``U`` with ``ValueError``.
    5. Final string must match ``^[0-9A-HJKMNP-TV-Z]{16}$``.
    """
    if not isinstance(key, str):
        raise ValueError("Enrollment key must be a string")
    s = key.strip().upper().replace("-", "")
    for ch in ("I", "L", "O", "U"):
        if ch in s:
            raise ValueError(
                "Enrollment key contains ambiguous character: "
                f"{ch} (I/L/O/U not allowed)"
            )
    if not CANONICAL_RE.match(s):
        raise ValueError("Enrollment key format invalid")
    return s


def format_display(canonical: str) -> str:
    """Return the hyphenated 19-char display form.

    Input MUST be the canonical 16-char form — call ``normalize()`` first
    if the source is untrusted.
    """
    return (
        f"{canonical[0:4]}-{canonical[4:8]}-"
        f"{canonical[8:12]}-{canonical[12:16]}"
    )


def derive_signing_subkey(normalized_key: str) -> bytes:
    """Derive a 32-byte HMAC signing subkey via blake2b.

    Must be called with the *normalized* key (16-char canonical form).
    The personalisation bytes pin the derivation to this protocol version
    — changing them invalidates every previously-enrolled token's HMAC.
    """
    return hashlib.blake2b(
        normalized_key.encode("utf-8"),
        person=HMAC_PERSONALIZATION,
        digest_size=32,
    ).digest()


def load_current() -> str:
    """Return the current 16-char canonical enrollment key.

    Pure ORM read; no caching — rotation from another process takes
    effect on the next call.
    """
    from .models import ManagerSettings
    return (
        ManagerSettings.objects
        .only("enrollment_key")
        .get(pk=1)
        .enrollment_key
    )


def rotate(new_key: str = None) -> str:
    """Persist a fresh enrollment key and return the display form.

    When ``new_key`` is ``None``, a cryptographically-random key is
    generated via ``generate_key()``.  When ``new_key`` is supplied, it
    is normalised first (raising ``ValueError`` on invalid input) and
    then written verbatim — this path exists for DR/recovery scenarios
    where the admin needs to sync keys across a secondary manager.

    The read-modify-write is serialised with a module-level lock so two
    concurrent calls from the same process can't silently overwrite each
    other.
    """
    from .models import ManagerSettings
    if new_key is None:
        candidate = generate_key()
    else:
        candidate = normalize(new_key)
    with _rotate_lock:
        row = ManagerSettings.objects.get(pk=1)
        row.enrollment_key = candidate
        row.save(
            update_fields=["enrollment_key", "enrollment_key_updated_at"]
        )
    return format_display(candidate)
