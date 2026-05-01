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

from django.core.management.base import BaseCommand, CommandError

from workers.management.commands.apply_pending_setup_db import (
    apply_atomic,
    scrub_payload_password,
)
from workers.management.commands.apply_pending_setup_helpers import (
    PENDING_SETUP_FILENAME,
    PROGRESS_FILENAME,
    PendingSetupError,
    PendingSetupGuardError,
    SentinelError,
    acquire_apply_lock,
    best_effort_unlink,
    emit_stderr_and_exit,
    is_pending_stale,
    post_apply_self_check,
    read_pending_setup,
    reread_password,
    rerun_self_check_for_recovery,
    schema_version_supported,
)
from workers.management.commands.apply_pending_setup_trust import (
    apply_filesystem_trust,
)

logger = logging.getLogger(__name__)


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
        try:
            self._apply_payload(
                payload, data_dir, pending_path, progress_path,
            )
        finally:
            # Spec 2 security LOW (review 6688ada): belt-and-suspenders.
            # _apply_payload's finally already mutates the dict in
            # place to scrub password_plaintext; here we also clear
            # this frame's reference so the dict is collectable.
            payload = None
            del payload

    def _handle_recovery_branches(
        self,
        sentinel_path: Path,
        pending_path: Path,
        progress_path: Path,
    ) -> bool:
        """FR-APPLY5: return True when the run is a no-op recovery."""
        if sentinel_path.exists() and pending_path.exists():
            # Spec 2 django/API LOW (review 6688ada): a previous apply
            # wrote the sentinel and failed BEFORE unlinking pending —
            # the likely failure point is the post-apply self-check.
            # Re-run it before declaring recovery successful.
            logger.info(
                "apply previously crashed between sentinel write and "
                "pending unlink; re-running self-check",
            )
            rerun_self_check_for_recovery(pending_path)
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
            apply_atomic(username, email, password)
        finally:
            # Spec 2 security LOW (review 6688ada): in-place dict scrub
            # so other frames still referencing this payload (notably
            # ``_run``) see ``password_plaintext = None``.  Then clear
            # this frame's locals.
            scrub_payload_password(payload)
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
