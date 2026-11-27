# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The worker page password hash (setup-rebuild FR-WK-3).

Django's ``pbkdf2_sha256`` text format, made and checked with the standard
library only, so the worker needs no Django and Django's ``check_password``
can still read the value::

    pbkdf2_sha256$<iterations>$<salt>$<base64 of the 32-byte PBKDF2-HMAC-SHA256>

The salt is 16 random bytes written as 32 lowercase hex characters: text from
Django's salt alphabet, with no ``$``, long enough that Django's
``must_update`` does not ask for a new salt (it wants at least 128 bits at
log2(62) bits per character, that is 22 characters). The iteration count is
Django 5.2's default, 1,000,000.

This module imports only the standard library, so ``tools/dev_worker.py``
can load it by file path without importing the worker package.
"""
import base64
import hashlib
import hmac
import re
import secrets
from typing import Optional, Tuple

ALGORITHM = 'pbkdf2_sha256'
ITERATIONS = 1_000_000
SALT_BYTES = 16
# A hand-edited count above this is refused instead of tying up a request thread.
MAX_ITERATIONS = 10_000_000

_SALT_RE = re.compile(r'[A-Za-z0-9]{1,128}')       # Django's RANDOM_STRING_CHARS; never '$'
_HASH_RE = re.compile(r'[A-Za-z0-9+/]{43}=')       # standard base64 of 32 bytes
_ITERATIONS_RE = re.compile(r'[1-9][0-9]{0,7}')


def _pbkdf2(password: str, salt: str, iterations: int) -> str:
    raw = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('ascii'), iterations)
    return base64.b64encode(raw).decode('ascii')


def make(password: str) -> str:
    """Hash ``password`` with a new random salt; the Django text format."""
    if not isinstance(password, str):
        raise TypeError('password must be a string')
    salt = secrets.token_hex(SALT_BYTES)
    return f'{ALGORITHM}${ITERATIONS}${salt}${_pbkdf2(password, salt, ITERATIONS)}'


def parse(encoded) -> Optional[Tuple[int, str, str]]:
    """``(iterations, salt, b64 hash)`` when ``encoded`` is a hash this module can check, else ``None``."""
    if not isinstance(encoded, str) or not encoded.isascii():
        return None
    parts = encoded.split('$')
    if len(parts) != 4 or parts[0] != ALGORITHM:
        return None
    _algorithm, iterations, salt, digest = parts
    if not (_ITERATIONS_RE.fullmatch(iterations) and _SALT_RE.fullmatch(salt) and _HASH_RE.fullmatch(digest)):
        return None
    count = int(iterations)
    if count > MAX_ITERATIONS:
        return None
    return count, salt, digest


def verify(password: str, encoded: str) -> bool:
    """True when ``password`` matches ``encoded`` (constant-time compare); False for an unusable hash."""
    parsed = parse(encoded)
    if parsed is None or not isinstance(password, str):
        return False
    iterations, salt, digest = parsed
    return hmac.compare_digest(_pbkdf2(password, salt, iterations), digest)
