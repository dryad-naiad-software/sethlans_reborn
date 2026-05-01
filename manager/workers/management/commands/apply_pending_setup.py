# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Management command: ``apply_pending_setup --data-dir DIR``.

Implements Spec 2 FR-APPLY1 ... FR-APPLY-LOG1.  The launcher invokes
this command in its own short-lived subprocess between observing
``.wizard_done`` and spawning the manager runtime.  It consumes
``<data-dir>/pending_setup.json`` exactly once and writes the
``.setup_complete`` sentinel.

Exit codes: ``0`` apply succeeded (or idempotent re-run no-op'd);
``1`` pre-apply guard failed (missing / stale / wrong-schema, lock
contention, bad arguments — no DB mutation); ``2`` operational
failure during steps 1-5 or self-check.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from workers.management.commands.apply_pending_setup_helpers import (
    PENDING_SETUP_FILENAME,
    PROGRESS_FILENAME,
    AdminCreateError,
    EnrollmentKeyError,
    PendingSetupError,
    PendingSetupGuardError,
    SentinelError,
    acquire_apply_lock,
    apply_filesystem_trust,
    best_effort_unlink,
    emit_stderr_and_exit,
    is_pending_stale,
    post_apply_self_check,
    read_pending_setup,
    reread_password,
    schema_version_supported,
)

logger = logging.getLogger(__name__)

User = get_user_model()


def _validate_data_dir(value: str) -> Path:
    """Argparse type for ``--data-dir``: absolute, existing, writable."""
    path = Path(value)
    if not path.is_absolute():
        raise CommandError("--data-dir must be an absolute path")
    if not path.exists():
        raise CommandError("--data-dir does not exist")
    if not os.access(str(path), os.W_OK):
        raise CommandError("--data-dir is not writable")
    return path


class Command(BaseCommand):
    help = (
        "Consume <data-dir>/pending_setup.json: create admin user, "
        "ensure enrollment key, write the setup-complete sentinel. "
        "Invoked once by the launcher after the wizard exits."
    )

    # FR-APPLY-INVOKE — argparse rejects unknown args by default; the
    # only non-Django-builtin argument is ``--data-dir``.
    def add_arguments(self, parser):
        parser.add_argument(
            "--data-dir",
            dest="data_dir",
            required=True,
            type=_validate_data_dir,
            help="Absolute path to the manager data directory.",
        )

    # ---- Top-level entry --------------------------------------------------

    def handle(self, *args, **options):
        data_dir: Path = options["data_dir"]
        try:
            self._run(data_dir)
        except PendingSetupGuardError as exc:
            logger.critical("apply pre-guard failed: %s", exc)
            emit_stderr_and_exit(f"apply guard: {exc}", 1)
            return  # unreachable
        except PendingSetupError as exc:
            logger.critical("apply operational failure: %s", exc)
            emit_stderr_and_exit(f"apply error: {exc}", 2)
            return  # unreachable
        except BaseException as exc:  # noqa: BLE001
            # FR-APPLY-LOG1: defeat default traceback printer so
            # plaintext password locals never leak via stderr.
            logger.critical(
                "apply unhandled %s", exc.__class__.__name__,
            )
            emit_stderr_and_exit(
                f"apply unhandled: {exc.__class__.__name__}", 2,
            )
            return  # unreachable

    # ---- Orchestration ---------------------------------------------------

    def _run(self, data_dir: Path) -> None:
        pending_path = data_dir / PENDING_SETUP_FILENAME
        progress_path = data_dir / PROGRESS_FILENAME
        sentinel_path = data_dir / ".setup_complete"

        # FR-APPLY1a — single-instance lock BEFORE any other action.
        if acquire_apply_lock(data_dir) is None:
            raise PendingSetupGuardError(
                "another apply or manager process is running against "
                "this data dir; refusing to start",
            )

        if self._handle_recovery_branches(
            sentinel_path, pending_path, progress_path,
        ):
            return

        payload = self._read_and_gate(pending_path)
        self._apply_payload(payload, data_dir, pending_path, progress_path)

    def _handle_recovery_branches(
        self,
        sentinel_path: Path,
        pending_path: Path,
        progress_path: Path,
    ) -> bool:
        """FR-APPLY5: return True when the run is a no-op recovery."""
        if sentinel_path.exists() and pending_path.exists():
            logger.info(
                "apply already complete; cleaning stale pending file",
            )
            best_effort_unlink(pending_path)
            best_effort_unlink(progress_path)
            return True
        if sentinel_path.exists() and not pending_path.exists():
            logger.info("setup already complete; nothing to apply")
            return True
        return False

    def _read_and_gate(self, pending_path: Path) -> dict:
        """FR-APPLY1b / FR-APPLY1c — schema + TTL gates."""
        payload = read_pending_setup(pending_path)
        if not schema_version_supported(payload):
            logger.critical(
                "pending_setup.json schema_version=%r not supported",
                payload.get("schema_version"),
            )
            raise PendingSetupGuardError(
                f"unsupported schema_version: "
                f"{payload.get('schema_version')!r}",
            )
        if is_pending_stale(payload):
            logger.critical("stale pending setup, re-run wizard")
            best_effort_unlink(pending_path)
            raise PendingSetupGuardError(
                "pending_setup.json TTL exceeded; file unlinked",
            )
        return payload

    def _apply_payload(
        self,
        payload: dict,
        data_dir: Path,
        pending_path: Path,
        progress_path: Path,
    ) -> None:
        """FR-APPLY2 + FR-APPLY3 — full apply + sentinel + self-check."""
        topology = payload.get("topology", "manager")
        admin = payload.get("admin_user") or {}
        username = admin.get("username", "")
        email = admin.get("email", "")
        password = admin.get("password_plaintext", "")
        auto_enroll = bool(payload.get("auto_enroll_local_worker", False))

        try:
            self._apply_atomic(username, email, password)
        finally:
            admin = None
            payload = None
            del admin
            del payload
            password = None
            del password

        if auto_enroll:
            apply_filesystem_trust()

        from workers.services.sentinel import create_sentinel
        try:
            create_sentinel(
                data_dir=data_dir, topology=topology, checkpoints=[],
            )
        except OSError as exc:
            raise SentinelError(
                f"sentinel write failed: {exc.__class__.__name__}",
            ) from None

        check_password = (
            reread_password(pending_path)
            if pending_path.exists()
            else None
        )
        try:
            post_apply_self_check(username, check_password)
        finally:
            check_password = None
            del check_password

        best_effort_unlink(pending_path)
        best_effort_unlink(progress_path)

    # ---- Steps 1-3 (atomic) ----------------------------------------------

    def _apply_atomic(
        self, username: str, email: str, password: str,
    ) -> None:
        """FR-APPLY2 steps 1-3 inside a single ``transaction.atomic``.

        Concurrency invariant (Spec 2 LOW): DB-mutating steps MUST run
        inside ``atomic()``; ``emit_stderr_and_exit`` uses ``os._exit``
        which skips ``finally`` — only ``atomic()`` rollback during
        unwind keeps state consistent.
        """
        try:
            with transaction.atomic():
                # Step 1 — defensive password validation.
                try:
                    validate_password(password)
                except ValidationError as exc:
                    raise AdminCreateError(
                        f"password validation failed for "
                        f"username={username!r}: {exc.messages}",
                    ) from None
                # Step 2 — create superuser (idempotent on duplicate).
                self._create_superuser(username, email, password)
                # Step 3 — enrollment key (idempotent).
                self._ensure_enrollment_key()
        except (AdminCreateError, EnrollmentKeyError):
            raise
        except Exception as exc:  # noqa: BLE001
            raise AdminCreateError(
                f"atomic apply failed: {exc.__class__.__name__}",
            ) from None

    def _create_superuser(
        self, username: str, email: str, password: str,
    ) -> None:
        try:
            User.objects.create_superuser(
                username=username, email=email, password=password,
            )
        except IntegrityError:
            # FR-APPLY2 step 2 — idempotent on existing username.
            logger.warning(
                "superuser %r already exists; skipping creation",
                username,
            )
            return
        except ValidationError as exc:
            raise AdminCreateError(
                f"create_superuser validation: {exc.messages}",
            ) from None
        except Exception as exc:  # noqa: BLE001
            raise AdminCreateError(
                f"create_superuser failed for username={username!r}: "
                f"{exc.__class__.__name__}",
            ) from None
        else:
            logger.info("superuser %r created", username)

    def _ensure_enrollment_key(self) -> None:
        """FR-APPLY2 step 3 — get-or-create singleton, ensure key set."""
        from workers import enrollment_key as ek
        from workers.models import ManagerSettings
        try:
            row, created = ManagerSettings.objects.get_or_create(pk=1)
            if created or not row.enrollment_key:
                row.enrollment_key = ek.generate_key()
                row.save(update_fields=[
                    "enrollment_key", "enrollment_key_updated_at",
                ])
                logger.info("enrollment key generated/persisted")
            else:
                logger.info("enrollment key already present; reusing")
        except Exception as exc:  # noqa: BLE001
            raise EnrollmentKeyError(
                f"ensure_enrollment_key: {exc.__class__.__name__}",
            ) from None
