# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""In-memory wizard step state (admin tuple, worker pw, ffmpeg metadata).

Phase 1 / Spec 2 — FR-M2-5 / FR-M2-6 / FR-M2-7. Per the spec the
admin password and worker UI password hash MUST live in process memory
between the step that captures them (admin-user / worker-password) and
the pending-setup serialization at FR-M2-9. They MUST NOT be written
to ``manager.ini``, the wizard log, or any sentinel — only to
``pending_setup.json`` (and only at FR-M2-9 time, atomically with
``chmod 600`` per FR-PEND-LIFECYCLE).

This module owns that mutable state. All access goes through the
public setters/getters which acquire ``_state_lock`` for read-modify-
write safety. The lock is independent of ``auth_state``'s singleton
handoff-state lock — Phase 1 handlers do NOT hold both at once, so
there is no AB-BA risk; documented here defensively.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)


# Lock for the in-memory state below. Held only during read or
# read-modify-write of the wizard_state slice.
_state_lock: threading.Lock = threading.Lock()

# Admin user tuple captured by FR-M2-5; cleared on wizard exit
# (process death) or via :func:`reset_state_for_tests`.
_admin_username: Optional[str] = None
_admin_email: Optional[str] = None
_admin_password_plaintext: Optional[str] = None

# Worker UI password (manager_worker only) — PBKDF2-HMAC-SHA-256 hex
# hash + 16-byte salt hex (FR-M2-6).
_worker_ui_password_hash: Optional[str] = None
_worker_ui_password_salt: Optional[str] = None

# FFmpeg install metadata captured at FR-M2-7 completion. The
# pending-setup handler reads these into the schema.
_ffmpeg_version: Optional[str] = None
_ffmpeg_binary_path: Optional[str] = None


def set_admin(username: str, email: str, password_plaintext: str) -> None:
    """Stash the admin tuple. Overwrites any prior tuple (idempotent)."""
    global _admin_username, _admin_email, _admin_password_plaintext
    if not isinstance(username, str) or not username:
        raise ValueError("admin username must be a non-empty str")
    if not isinstance(email, str) or not email:
        raise ValueError("admin email must be a non-empty str")
    if not isinstance(password_plaintext, str) or not password_plaintext:
        raise ValueError("admin password must be a non-empty str")
    with _state_lock:
        _admin_username = username
        _admin_email = email
        _admin_password_plaintext = password_plaintext
    # NEVER log the password or email body. Username only is fine.
    logger.info("Wizard captured admin tuple for user %s", username)


def get_admin() -> Optional[dict]:
    """Return ``{"username","email","password_plaintext"}`` or None."""
    with _state_lock:
        if not _admin_username:
            return None
        return {
            "username": _admin_username,
            "email": _admin_email or "",
            "password_plaintext": _admin_password_plaintext or "",
        }


def clear_admin() -> None:
    """Drop the admin tuple from memory (called from FR-W17 polite exit)."""
    global _admin_username, _admin_email, _admin_password_plaintext
    with _state_lock:
        _admin_username = None
        _admin_email = None
        _admin_password_plaintext = None


def set_worker_password_hash(hash_hex: str, salt_hex: str) -> None:
    """Stash the worker UI password hash + salt (FR-M2-6).

    Both are hex-encoded. A fresh salt is generated on each submit.
    """
    global _worker_ui_password_hash, _worker_ui_password_salt
    if not isinstance(hash_hex, str) or not hash_hex:
        raise ValueError("hash_hex must be a non-empty str")
    if not isinstance(salt_hex, str) or not salt_hex:
        raise ValueError("salt_hex must be a non-empty str")
    with _state_lock:
        _worker_ui_password_hash = hash_hex
        _worker_ui_password_salt = salt_hex
    logger.info("Wizard captured worker UI password hash + salt")


def get_worker_password() -> Optional[dict]:
    """Return ``{"hash","salt"}`` or None when not set."""
    with _state_lock:
        if not _worker_ui_password_hash or not _worker_ui_password_salt:
            return None
        return {
            "hash": _worker_ui_password_hash,
            "salt": _worker_ui_password_salt,
        }


def set_ffmpeg(version: str, binary_path: str) -> None:
    """Stash the FFmpeg install metadata (FR-M2-7 complete branch)."""
    global _ffmpeg_version, _ffmpeg_binary_path
    if not isinstance(version, str) or not version:
        raise ValueError("ffmpeg version must be a non-empty str")
    if not isinstance(binary_path, str) or not binary_path:
        raise ValueError("ffmpeg binary_path must be a non-empty str")
    with _state_lock:
        _ffmpeg_version = version
        _ffmpeg_binary_path = binary_path
    logger.info("Wizard captured FFmpeg install at %s", binary_path)


def get_ffmpeg() -> Optional[dict]:
    """Return ``{"version","binary_path"}`` or None when not set."""
    with _state_lock:
        if not _ffmpeg_version or not _ffmpeg_binary_path:
            return None
        return {
            "version": _ffmpeg_version,
            "binary_path": _ffmpeg_binary_path,
        }


def reset_state_for_tests() -> None:
    """Wipe every slice. Tests share module-level state across the run."""
    global _admin_username, _admin_email, _admin_password_plaintext
    global _worker_ui_password_hash, _worker_ui_password_salt
    global _ffmpeg_version, _ffmpeg_binary_path
    with _state_lock:
        _admin_username = None
        _admin_email = None
        _admin_password_plaintext = None
        _worker_ui_password_hash = None
        _worker_ui_password_salt = None
        _ffmpeg_version = None
        _ffmpeg_binary_path = None


__all__ = [
    "set_admin",
    "get_admin",
    "clear_admin",
    "set_worker_password_hash",
    "get_worker_password",
    "set_ffmpeg",
    "get_ffmpeg",
    "reset_state_for_tests",
]
