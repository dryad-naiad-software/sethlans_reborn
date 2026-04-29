# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Stdlib password validators (FR-M2-5).

Mirrors the four validators the manager configures in
``manager/sethlans_manager/settings.py:186-191``:

* ``MinimumLengthValidator`` — ``len(password) >= 8``.
* ``UserAttributeSimilarityValidator`` — fuzzy compare against
  ``username``, ``email``, and the email's local-part using
  ``difflib.SequenceMatcher.quick_ratio() < 0.7``.
* ``CommonPasswordValidator`` — set membership against the bundled
  resource ``data/common-passwords.txt``.
* ``NumericPasswordValidator`` — ``not password.isdigit()``.

Implementation rules:

* Length cap (security-reviewer LOW-7): reject ``len(password) > 4096``
  BEFORE running the similarity check to bound ``difflib`` time.
* Resource integrity (security-reviewer LOW-6): the common-passwords
  file SHA-256 is verified at startup against
  :data:`COMMON_PASSWORDS_SHA256`. Mismatch is fail-closed — the
  validator returns ``"common_passwords_resource_invalid"`` from every
  call until the file is restored.
* No Django imports. No vendored Django subset.
"""

from __future__ import annotations

import difflib
import hashlib
import logging
import threading
from importlib.resources import files
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

# ---- Tunables / pinned constants ----

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 4096  # security-reviewer LOW-7 — difflib DoS bound
SIMILARITY_THRESHOLD = 0.7

# SHA-256 of ``wizard/sethlans_wizard/data/common-passwords.txt``. The
# resource is shipped as plain text (one lowercase password per line,
# LF newline terminator on every line including the last). The hash
# is recomputed when the resource is regenerated from a fresh Django
# snapshot — see Phase 1 progress notes for the regeneration command.
COMMON_PASSWORDS_SHA256 = (
    "29ca0fa5303165f012f3e9775e3e95a3071cdd59f219973ec1cbb308d0214a6f"
)

_RESOURCE_PACKAGE = "wizard.sethlans_wizard.data"
_RESOURCE_NAME = "common-passwords.txt"


# ---- Resource loader (lazy + integrity-checked) ----

_resource_lock: threading.Lock = threading.Lock()
_common_passwords_cache: Optional[frozenset[str]] = None
_resource_load_error: Optional[str] = None


def _load_common_passwords() -> Optional[frozenset[str]]:
    """Load the common-passwords resource and verify its SHA-256.

    Returns the password set on success, or None if the resource is
    missing or corrupted. The cached result (success or failure) is
    held under ``_resource_lock`` so subsequent calls don't re-read
    the file.
    """
    global _common_passwords_cache, _resource_load_error
    with _resource_lock:
        if _common_passwords_cache is not None:
            return _common_passwords_cache
        if _resource_load_error is not None:
            return None
        try:
            resource_path = files(_RESOURCE_PACKAGE).joinpath(_RESOURCE_NAME)
            data = resource_path.read_bytes()
        except (FileNotFoundError, OSError, ModuleNotFoundError) as exc:
            _resource_load_error = (
                f"common-passwords resource missing: {exc}"
            )
            logger.critical(_resource_load_error)
            return None
        actual = hashlib.sha256(data).hexdigest()
        if actual != COMMON_PASSWORDS_SHA256:
            _resource_load_error = (
                "common-passwords resource SHA-256 mismatch "
                f"(expected {COMMON_PASSWORDS_SHA256}, got {actual})"
            )
            logger.critical(_resource_load_error)
            return None
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            _resource_load_error = (
                f"common-passwords resource not valid UTF-8: {exc}"
            )
            logger.critical(_resource_load_error)
            return None
        words = {ln.strip() for ln in text.splitlines() if ln.strip()}
        _common_passwords_cache = frozenset(words)
        logger.info(
            "Common-passwords resource loaded (%d entries)",
            len(_common_passwords_cache),
        )
        return _common_passwords_cache


def verify_resource() -> Optional[str]:
    """Verify the common-passwords resource is present + intact.

    Returns ``None`` on success or an error code string on failure.
    Wizard startup MUST call this and refuse to advance on a non-None
    return.
    """
    if _load_common_passwords() is None:
        return "common_passwords_resource_invalid"
    return None


# ---- Individual validators (return None on pass, str code on fail) ----

def _check_length(password: str) -> Optional[str]:
    if len(password) > MAX_PASSWORD_LENGTH:
        return "password_too_long"
    if len(password) < MIN_PASSWORD_LENGTH:
        return "password_too_short"
    return None


def _check_user_attribute_similarity(
    password: str,
    user_attrs: Iterable[str],
) -> Optional[str]:
    pw_lower = password.lower()
    for attr in user_attrs:
        if not isinstance(attr, str) or not attr:
            continue
        attr_lower = attr.lower()
        ratio = difflib.SequenceMatcher(None, pw_lower, attr_lower).quick_ratio()
        if ratio >= SIMILARITY_THRESHOLD:
            return "password_too_similar"
        # Email local-part — compare separately so a match against the
        # domain doesn't shadow a legitimate similarity to the user-id.
        if "@" in attr_lower:
            local = attr_lower.split("@", 1)[0]
            if local and local != attr_lower:
                ratio2 = difflib.SequenceMatcher(
                    None, pw_lower, local,
                ).quick_ratio()
                if ratio2 >= SIMILARITY_THRESHOLD:
                    return "password_too_similar"
    return None


def _check_common_password(password: str) -> Optional[str]:
    common = _load_common_passwords()
    if common is None:
        return "common_passwords_resource_invalid"
    if password.lower() in common:
        return "password_too_common"
    return None


def _check_numeric(password: str) -> Optional[str]:
    if password.isdigit():
        return "password_entirely_numeric"
    return None


def validate_password(
    password: str,
    user_attrs: Optional[Iterable[str]] = None,
) -> list[str]:
    """Run every configured validator and return the failure codes.

    Returns an empty list on full pass. Codes are stable strings the
    handler maps to user-facing messages.

    The length cap (LOW-7) runs FIRST so the similarity check below
    cannot run on a 100 MB payload.
    """
    if not isinstance(password, str):
        return ["password_not_a_string"]
    failures: list[str] = []
    length_failure = _check_length(password)
    if length_failure == "password_too_long":
        # Bail immediately — never feed a giant string into difflib.
        return [length_failure]
    if length_failure:
        failures.append(length_failure)
    sim_failure = _check_user_attribute_similarity(password, user_attrs or [])
    if sim_failure:
        failures.append(sim_failure)
    common_failure = _check_common_password(password)
    if common_failure:
        failures.append(common_failure)
    numeric_failure = _check_numeric(password)
    if numeric_failure:
        failures.append(numeric_failure)
    return failures


def reset_resource_cache_for_tests() -> None:
    """Wipe the resource-load cache. Test-only helper."""
    global _common_passwords_cache, _resource_load_error
    with _resource_lock:
        _common_passwords_cache = None
        _resource_load_error = None


__all__ = [
    "MIN_PASSWORD_LENGTH",
    "MAX_PASSWORD_LENGTH",
    "SIMILARITY_THRESHOLD",
    "COMMON_PASSWORDS_SHA256",
    "verify_resource",
    "validate_password",
    "reset_resource_cache_for_tests",
]
