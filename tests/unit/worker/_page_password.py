# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Helpers for the worker page password tests (setup-rebuild FR-WK-3, FR-SEC-8).

- ``fast_hash(password)``: a stored hash in the real Django ``pbkdf2_sha256``
  text format, made with ``FAST_ROUNDS`` instead of 1,000,000 so a check
  costs microseconds. ``password_hash.parse`` accepts any count from 1, so
  ``auth.check_bearer`` runs its real path against it.
- ``write_hash(folder, value)``: put ``worker.ui_password_hash`` into
  ``<folder>/config.json`` (the folder ``SETHLANS_WORKER_DATA_DIR`` names).

Module name starts with an underscore so pytest does not collect it.
"""
import base64
import hashlib
import json

FAST_ROUNDS = 1000
SALT = 'testsalt0123456789abcdef01234567'


def fast_hash(password: str, salt: str = SALT) -> str:
    raw = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('ascii'), FAST_ROUNDS)
    return f"pbkdf2_sha256${FAST_ROUNDS}${salt}${base64.b64encode(raw).decode('ascii')}"


def write_hash(folder, value) -> None:
    (folder / 'config.json').write_text(json.dumps({'worker': {'ui_password_hash': value}}), encoding='utf-8')
