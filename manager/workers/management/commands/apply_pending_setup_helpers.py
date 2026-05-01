# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Helpers for ``apply_pending_setup`` (FR-APPLY1 ... FR-APPLY-LOG1).

Split out so the command file stays under the 300-line ceiling.
Covers: ``PendingSetupError`` hierarchy (FR-APPLY-LOG1),
cross-platform single-instance lock (FR-APPLY1a), schema + TTL gate
readers (FR-APPLY1b / FR-APPLY1c).
"""

from __future__ import annotations

import json
import logging
import os
import platform
import sys
import time
from pathlib import Path
from typing import Optional  # noqa: F401 — used in helper signatures

logger = logging.getLogger(__name__)

PENDING_SETUP_FILENAME = "pending_setup.json"
APPLY_LOCK_FILENAME = ".apply.lock"
PROGRESS_FILENAME = ".setup_progress.json"
SUPPORTED_SCHEMA_VERSIONS = {1}
PENDING_SETUP_MAX_AGE_SECONDS = 86_400


# ---- Sanitised exception hierarchy (FR-APPLY-LOG1) ------------------------

class PendingSetupError(Exception):
    """Base sanitised apply error.  Subclasses MUST NOT carry the password."""
    code = "pending_setup_error"


class AdminCreateError(PendingSetupError):
    code = "admin_create_failed"


class EnrollmentKeyError(PendingSetupError):
    code = "enrollment_key_failed"


class FilesystemTrustError(PendingSetupError):
    code = "filesystem_trust_failed"


class SentinelError(PendingSetupError):
    code = "sentinel_write_failed"


class SelfCheckError(PendingSetupError):
    code = "self_check_failed"


class PendingSetupGuardError(PendingSetupError):
    """Pre-apply guard failure (schema, TTL, lock, missing file, args)."""
    code = "pre_apply_guard_failed"


# ---- Single-instance lock (FR-APPLY1a) ------------------------------------

class _LockHandle:
    """Holds an acquired lock fd; never explicitly closed (OS releases at exit)."""

    def __init__(self, fd: int, path: Path):
        self.fd = fd
        self.path = path


def acquire_apply_lock(data_dir: Path) -> Optional[_LockHandle]:
    """Acquire ``<data_dir>/.apply.lock`` (FR-APPLY1a).

    Returns the handle on success; ``None`` if another process holds it.
    POSIX uses ``fcntl.flock`` LOCK_EX|LOCK_NB, Windows uses
    ``msvcrt.locking`` LK_NBLCK. fd is never closed by callers.
    """
    lock_path = Path(data_dir) / APPLY_LOCK_FILENAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if platform.system() == "Windows":
            import msvcrt
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError:
                os.close(fd)
                return None
        else:
            import fcntl
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                return None
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    return _LockHandle(fd=fd, path=lock_path)


# ---- Pending file readers (FR-APPLY1b / FR-APPLY1c) -----------------------

def read_pending_setup(path: Path) -> dict:
    """Read + parse ``pending_setup.json``; raise ``PendingSetupGuardError``."""
    if not path.exists():
        raise PendingSetupGuardError("pending_setup.json missing")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PendingSetupGuardError(
            f"pending_setup.json unreadable: {exc.__class__.__name__}",
        ) from None
    if not isinstance(data, dict):
        raise PendingSetupGuardError(
            "pending_setup.json malformed (not a JSON object)",
        )
    return data


def schema_version_supported(payload: dict) -> bool:
    """Return ``True`` when ``schema_version`` is in the supported set."""
    return payload.get("schema_version") in SUPPORTED_SCHEMA_VERSIONS


def is_pending_stale(payload: dict, now: Optional[float] = None) -> bool:
    """FR-APPLY1c TTL gate: ``True`` when ``created_at_unix`` is too old."""
    created = payload.get("created_at_unix")
    if not isinstance(created, (int, float)):
        return True
    if now is None:
        now = time.time()
    return (now - float(created)) > PENDING_SETUP_MAX_AGE_SECONDS


# ---- Best-effort filesystem cleanup ---------------------------------------

def best_effort_unlink(path: Path) -> None:
    """``os.unlink(path)``; log WARN on OSError, do NOT raise."""
    try:
        os.unlink(str(path))
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.warning("Could not unlink %s: %s", path, exc)


# ---- Self-check (FR-APPLY3) ------------------------------------------------

def post_apply_self_check(
    username: str, password: Optional[str],
) -> None:
    """Confirm ``authenticate()`` round-trip + enrollment key landed."""
    from django.contrib.auth import authenticate

    from workers.models import ManagerSettings
    try:
        row = ManagerSettings.objects.get(pk=1)
    except ManagerSettings.DoesNotExist:
        raise SelfCheckError(
            "self-check: ManagerSettings missing post-apply",
        ) from None
    if not row.enrollment_key:
        raise SelfCheckError(
            "self-check: enrollment_key empty post-apply",
        )
    if password is None:
        logger.warning(
            "self-check: pending file gone; skipped authenticate()",
        )
        return
    try:
        user = authenticate(username=username, password=password)
    except Exception as exc:  # noqa: BLE001
        raise SelfCheckError(
            f"authenticate() raised: {exc.__class__.__name__}",
        ) from None
    if user is None or not user.is_active:
        raise SelfCheckError(
            f"authenticate() returned None or inactive for "
            f"username={username!r}",
        )


def reread_password(pending_path: Path) -> Optional[str]:
    """Re-read the plaintext password for the self-check only.

    Returns ``None`` if the file is gone (best-effort).
    """
    try:
        payload = read_pending_setup(pending_path)
    except PendingSetupGuardError:
        return None
    admin = payload.get("admin_user") or {}
    return admin.get("password_plaintext")


def rerun_self_check_for_recovery(pending_path: Path) -> None:
    """Re-run post-apply self-check during sentinel+pending recovery.

    Spec 2 django/API LOW (review 6688ada): the old recovery branch
    silently masked a previously-failed self-check; re-run it so
    :class:`SelfCheckError` can propagate (command exits 2).
    """
    check_password = reread_password(pending_path)
    try:
        payload = read_pending_setup(pending_path)
        username = (payload.get("admin_user") or {}).get("username", "")
        post_apply_self_check(username, check_password)
    finally:
        check_password = None
        del check_password


# ---- Stderr emission (FR-APPLY-LOG1) --------------------------------------

def emit_stderr_and_exit(message: str, code: int) -> None:
    """Write *message* to stderr then ``os._exit(code)``.

    Spec 2 LOW invariant: ``os._exit`` skips ``finally`` and
    ``transaction.atomic`` rollbacks; DB-mutating steps MUST run
    inside ``atomic()`` (see ``apply_pending_setup_db.apply_atomic``).
    """
    sys.stderr.write(message)
    if not message.endswith("\n"):
        sys.stderr.write("\n")
    sys.stderr.flush()
    os._exit(code)


__all__ = [
    "APPLY_LOCK_FILENAME",
    "PENDING_SETUP_FILENAME",
    "PENDING_SETUP_MAX_AGE_SECONDS",
    "PROGRESS_FILENAME",
    "SUPPORTED_SCHEMA_VERSIONS",
    "AdminCreateError",
    "EnrollmentKeyError",
    "FilesystemTrustError",
    "PendingSetupError",
    "PendingSetupGuardError",
    "SelfCheckError",
    "SentinelError",
    "_LockHandle",
    "acquire_apply_lock",
    "best_effort_unlink",
    "emit_stderr_and_exit",
    "is_pending_stale",
    "post_apply_self_check",
    "read_pending_setup",
    "reread_password",
    "rerun_self_check_for_recovery",
    "schema_version_supported",
]
