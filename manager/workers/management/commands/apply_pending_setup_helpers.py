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

    Returns the handle on success; ``None`` if another process holds
    it.  POSIX uses ``fcntl.flock(LOCK_EX|LOCK_NB)``, Windows uses
    ``msvcrt.locking(LK_NBLCK, 1)``.  The fd is NEVER closed by
    callers; process exit releases the OS lock.
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


# ---- Filesystem-trust enrollment (FR-APPLY2 step 4) -----------------------

def prime_runtime_state_for_auto_enroll() -> None:
    """Populate ``runtime_state`` so ``auto_enroll_local_worker`` works.

    Reads manager_id from the DB and the cert fingerprint from on-disk
    TLS material (generated on first call). The apply subprocess does
    not boot through ``run_manager.py``, so both start as ``None``.

    Invariant (Spec 2 security MED, res. A): ``dev_mode=False`` is
    correct. The launcher has no ``--dev`` flag and is the only caller
    of this subprocess; dev mode is a ``run_manager.py --dev`` path
    handled by ``_dispatch_dev_mode`` and never invokes apply. Adding
    dev plumbing would require widening the FR-APPLY-INVOKE allowlist
    (currently ``--data-dir`` only).
    """
    from django.conf import settings as dj_settings
    from sethlans_manager import runtime_state
    from sethlans_manager.cert_utils import get_cert_fingerprint
    from sethlans_manager.tls_setup import setup_certificates
    from workers.models import ManagerSettings

    if runtime_state.manager_id is None:
        runtime_state.manager_id = ManagerSettings.objects.get(pk=1).manager_id
    if runtime_state.cert_fingerprint is None:
        manager_dir = Path(dj_settings.BASE_DIR)
        _, _, cert = setup_certificates(
            dev_mode=False,  # see invariant in docstring above
            manager_dir=manager_dir,
            project_root=manager_dir.parent,
        )
        runtime_state.cert_fingerprint = get_cert_fingerprint(cert)


def apply_filesystem_trust() -> None:
    """FR-APPLY2 step 4 — write the co-located worker's config.json."""
    from workers.services import auto_enroll, filesystem_trust
    try:
        prime_runtime_state_for_auto_enroll()
        envelope = auto_enroll.auto_enroll_local_worker()
        filesystem_trust.write_worker_config(
            config_path=filesystem_trust.get_worker_config_path(),
            api_token=envelope["api_token"],
            cert_fingerprint=envelope["cert_fingerprint"],
            manager_url=envelope["manager_url"],
            manager_id=envelope["manager_id"],
        )
    except Exception as exc:  # noqa: BLE001
        raise FilesystemTrustError(
            f"filesystem trust enrollment failed: "
            f"{exc.__class__.__name__}",
        ) from None


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


# ---- Stderr emission (FR-APPLY-LOG1) --------------------------------------

def emit_stderr_and_exit(message: str, code: int) -> None:
    """Write *message* to stderr; ``os._exit(code)`` (bypasses traceback printer).

    Concurrency invariant (Spec 2 LOW): ``os._exit`` skips ``finally``
    and transaction rollbacks — DB-mutating steps MUST run inside
    ``transaction.atomic()`` so the rollback fires during exception
    unwind. See ``_apply_atomic`` in ``apply_pending_setup``.
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
    "apply_filesystem_trust",
    "best_effort_unlink",
    "emit_stderr_and_exit",
    "is_pending_stale",
    "post_apply_self_check",
    "prime_runtime_state_for_auto_enroll",
    "read_pending_setup",
    "reread_password",
    "schema_version_supported",
]
