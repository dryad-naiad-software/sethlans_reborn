# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The rules a worker page password must pass (setup-rebuild FR-WK-3, FR-WK-4, FR-WZ-6).

Django's validators cannot run on the worker (it has no Django), so the
manager's four are copied here with Django 5.2's own logic and messages:

- ``MinimumLengthValidator``: at least 8 characters.
- ``CommonPasswordValidator``: not in Django 5.2's list of 20,000 common
  passwords, shipped unchanged in ``common_passwords/`` (BSD-3-Clause, notice
  in ``common_passwords/LICENSE.txt``), compared stripped and lowercased. The
  list is read once, on the first check; a missing list raises
  ``FileNotFoundError`` (a broken install), never a silent pass.
- ``NumericPasswordValidator``: not entirely digits (``str.isdigit``).
- ``UserAttributeSimilarityValidator`` (``max_similarity`` 0.7) against this
  computer's name instead of a user's attributes: the name and each part of
  it split on non-word characters, compared with ``difflib.SequenceMatcher``
  ``quick_ratio``, skipping parts that are too short to matter (Django's
  ``exceeds_maximum_length_ratio``).

One rule of the worker's own: the password goes in an ``Authorization``
header, which a browser can only send as Latin-1 text, so every character
must be printable Latin-1 (U+0020 to U+007E or U+00A0 to U+00FF), with no
space at either end (header values are trimmed).
"""
import gzip
import re
import threading
from difflib import SequenceMatcher
from pathlib import Path
from typing import FrozenSet, List, Optional

MIN_LENGTH = 8
MAX_SIMILARITY = 0.7

TOO_SHORT = f"This password is too short. It must contain at least {MIN_LENGTH} characters."
TOO_COMMON = "This password is too common."
ENTIRELY_NUMERIC = "This password is entirely numeric."
TOO_SIMILAR = "The password is too similar to the computer name."
NOT_SENDABLE = ("This password has a character a browser cannot send: use letters, digits, punctuation and spaces "
                "from Latin-1 only.")
EDGE_SPACE = "This password cannot start or end with a space."


COMMON_PASSWORDS_PATH = Path(__file__).resolve().parent / 'common_passwords' / 'common-passwords.txt.gz'

_common_lock = threading.Lock()
_common: Optional[FrozenSet[str]] = None  # loaded once, under _common_lock


def _common_passwords() -> FrozenSet[str]:
    """Django's common-password list, read on first use (``FileNotFoundError`` when it is missing)."""
    global _common
    with _common_lock:
        if _common is None:
            with gzip.open(COMMON_PASSWORDS_PATH, 'rt', encoding='utf-8') as handle:
                _common = frozenset(line.strip() for line in handle)
        return _common


def _too_common(password: str) -> bool:
    """Django's ``CommonPasswordValidator.validate``: the stripped, lowercased password is on the list."""
    return password.lower().strip() in _common_passwords()


def _exceeds_maximum_length_ratio(password: str, value: str) -> bool:
    """Django's ``exceeds_maximum_length_ratio``: ``value`` is too short to make ``password`` similar."""
    pwd_len = len(password)
    length_bound_similarity = MAX_SIMILARITY / 2 * pwd_len
    value_len = len(value)
    return pwd_len >= 10 * value_len and value_len < length_bound_similarity


def _too_similar(password: str, hostname: Optional[str]) -> bool:
    if not hostname or not isinstance(hostname, str):
        return False
    password = password.lower()
    value_lower = hostname.lower()
    for value_part in re.split(r"\W+", value_lower) + [value_lower]:
        if _exceeds_maximum_length_ratio(password, value_part):
            continue
        if SequenceMatcher(a=password, b=value_part).quick_ratio() >= MAX_SIMILARITY:
            return True
    return False


def _sendable(password: str) -> bool:
    return all('\x20' <= ch <= '\x7e' or '\xa0' <= ch <= '\xff' for ch in password)


def problems(password, hostname: Optional[str]) -> List[str]:
    """Every rule ``password`` breaks, as plain sentences; an empty list when it passes."""
    if not isinstance(password, str):
        return ["The password must be text."]
    found = []
    if len(password) < MIN_LENGTH:
        found.append(TOO_SHORT)
    if _too_common(password):
        found.append(TOO_COMMON)
    if password.isdigit():
        found.append(ENTIRELY_NUMERIC)
    if _too_similar(password, hostname):
        found.append(TOO_SIMILAR)
    if not _sendable(password):
        found.append(NOT_SENDABLE)
    elif password != password.strip(' '):
        found.append(EDGE_SPACE)
    return found
