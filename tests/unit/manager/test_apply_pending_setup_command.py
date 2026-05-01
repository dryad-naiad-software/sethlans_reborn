# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Tests for the ``apply_pending_setup`` Django management command.

Covers FR-APPLY1 ... FR-APPLY-LOG1 — schema/TTL gates, atomic apply,
filesystem-trust path, idempotent re-run, lock contention, sanitised
error surface, and the no-password-in-logs invariant.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model

from workers.management.commands import (
    apply_pending_setup as apply_cmd,
    apply_pending_setup_helpers as helpers,
)


User = get_user_model()

# A password that easily satisfies Django's default validators.
STRONG_PASSWORD = "Tr0pical!Mongoose-Hops42"


def _write_pending(
    data_dir: Path,
    *,
    schema_version: int = 1,
    topology: str = "manager",
    auto_enroll: bool = False,
    created_at: float | None = None,
    username: str = "admin1",
    email: str = "admin@example.com",
    password: str = STRONG_PASSWORD,
) -> Path:
    """Write a pending_setup.json fixture file to *data_dir*."""
    if created_at is None:
        created_at = time.time()
    payload = {
        "schema_version": schema_version,
        "topology": topology,
        "created_at_unix": created_at,
        "admin_user": {
            "username": username,
            "email": email,
            "password_plaintext": password,
        },
        "worker_ui_password_hash": None,
        "worker_ui_password_salt": None,
        "auto_enroll_local_worker": auto_enroll,
    }
    target = data_dir / helpers.PENDING_SETUP_FILENAME
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return target


class _ExitCalled(SystemExit):
    """Replacement for ``os._exit`` so tests can observe the exit code."""


@pytest.fixture(autouse=True)
def _patch_os_exit(monkeypatch):
    """Convert ``os._exit`` into a ``SystemExit`` so pytest can catch it.

    The command's top-level wrapper calls ``os._exit`` to defeat
    Python's traceback printer (FR-APPLY-LOG1).  In tests we need a
    raisable exception so we can assert the exit code.
    """
    def fake_exit(code):
        raise _ExitCalled(code)
    monkeypatch.setattr(
        "workers.management.commands.apply_pending_setup_helpers.os._exit",
        fake_exit,
    )


def _run_command(
    data_dir: Path,
    *,
    extra_args: list[str] | None = None,
):
    """Invoke ``Command.handle`` with ``--data-dir`` parsed via argparse."""
    from django.core.management import call_command
    args = ["apply_pending_setup", "--data-dir", str(data_dir)]
    if extra_args:
        args.extend(extra_args)
    call_command(*args)


# ---- Argument parser allowlist (FR-APPLY-INVOKE) -------------------------

class TestArgumentAllowlist:
    """argparse rejects undeclared flags; only ``--data-dir`` is allowed."""

    def test_unknown_arg_raises(self, tmp_path):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        with pytest.raises((CommandError, SystemExit)):
            call_command(
                "apply_pending_setup",
                "--data-dir", str(tmp_path),
                "--whatever", "foo",
            )

    def test_missing_data_dir_raises(self):
        from django.core.management import call_command
        with pytest.raises((SystemExit, Exception)):
            call_command("apply_pending_setup")

    def test_data_dir_must_exist(self, tmp_path):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        ghost = tmp_path / "does-not-exist"
        with pytest.raises((CommandError, SystemExit)):
            call_command("apply_pending_setup", "--data-dir", str(ghost))


# ---- Pre-apply guards ----------------------------------------------------

class TestPreApplyGuards:
    """FR-APPLY1b / FR-APPLY1c / FR-APPLY1a."""

    @pytest.mark.django_db
    def test_schema_version_mismatch_does_not_delete_file(
        self, tmp_path, capsys,
    ):
        target = _write_pending(tmp_path, schema_version=999)
        with pytest.raises(SystemExit) as exc:
            _run_command(tmp_path)
        assert exc.value.code == 1
        # FR-APPLY1b — file must NOT be deleted on schema mismatch.
        assert target.exists()

    @pytest.mark.django_db
    def test_stale_ttl_deletes_file_and_exits_1(self, tmp_path):
        # 25 hours old — beyond PENDING_SETUP_MAX_AGE_SECONDS.
        old = time.time() - (25 * 3600)
        target = _write_pending(tmp_path, created_at=old)
        with pytest.raises(SystemExit) as exc:
            _run_command(tmp_path)
        assert exc.value.code == 1
        assert not target.exists()

    @pytest.mark.django_db
    def test_lock_contention_exit_1(self, tmp_path, mocker):
        _write_pending(tmp_path)
        mocker.patch.object(
            apply_cmd, "acquire_apply_lock", return_value=None,
        )
        with pytest.raises(SystemExit) as exc:
            _run_command(tmp_path)
        assert exc.value.code == 1


# ---- Happy paths ---------------------------------------------------------

class TestHappyPath:
    """FR-APPLY2 — the full apply sequence."""

    @pytest.mark.django_db
    def test_topology_manager_creates_user_and_sentinel(
        self, tmp_path,
    ):
        _write_pending(tmp_path, topology="manager", username="alice")
        _run_command(tmp_path)
        assert User.objects.filter(username="alice").exists()
        assert (tmp_path / ".setup_complete").exists()
        assert not (tmp_path / "pending_setup.json").exists()

    @pytest.mark.django_db
    def test_enrollment_key_persisted(self, tmp_path):
        _write_pending(tmp_path, username="bob")
        _run_command(tmp_path)
        from workers.models import ManagerSettings
        row = ManagerSettings.objects.get(pk=1)
        assert row.enrollment_key

    @pytest.mark.django_db
    def test_topology_manager_worker_with_auto_enroll(
        self, tmp_path, mocker,
    ):
        # Stub the filesystem-trust step — its inputs (runtime_state)
        # are populated end-to-end inside apply_filesystem_trust which
        # we exercise directly elsewhere.
        mock_apply = mocker.patch.object(
            apply_cmd, "apply_filesystem_trust",
        )
        _write_pending(
            tmp_path,
            topology="manager_worker",
            auto_enroll=True,
            username="charlie",
        )
        _run_command(tmp_path)
        assert mock_apply.called
        assert User.objects.filter(username="charlie").exists()
        assert (tmp_path / ".setup_complete").exists()


# ---- Idempotency / crash recovery (FR-APPLY5) ----------------------------

class TestIdempotency:

    @pytest.mark.django_db
    def test_second_run_is_noop(self, tmp_path, mocker):
        # In production each apply runs in its own subprocess (fresh
        # lock fd per call); in-process the helper retains the fd
        # between calls.  Patch the lock acquirer so each run sees a
        # successful acquire.
        mocker.patch.object(
            apply_cmd, "acquire_apply_lock",
            return_value=helpers._LockHandle(fd=-1, path=tmp_path),
        )
        _write_pending(tmp_path, username="dora")
        _run_command(tmp_path)
        _run_command(tmp_path)
        assert User.objects.filter(username="dora").exists()
        assert (tmp_path / ".setup_complete").exists()

    @pytest.mark.django_db
    def test_crash_recovery_branch_cleans_stale_pending(
        self, tmp_path, mocker,
    ):
        mocker.patch.object(
            apply_cmd, "acquire_apply_lock",
            return_value=helpers._LockHandle(fd=-1, path=tmp_path),
        )
        from workers.services.sentinel import create_sentinel
        create_sentinel(
            data_dir=tmp_path, topology="manager", checkpoints=[],
        )
        pending = _write_pending(tmp_path, username="ed")
        _run_command(tmp_path)
        assert not pending.exists()
        assert (tmp_path / ".setup_complete").exists()


# ---- Self-check (FR-APPLY3) ----------------------------------------------

class TestSelfCheck:

    @pytest.mark.django_db
    def test_self_check_failure_exits_2(self, tmp_path, mocker):
        _write_pending(tmp_path, username="frank")
        # Force the self-check to raise SelfCheckError.
        mocker.patch.object(
            apply_cmd, "post_apply_self_check",
            side_effect=helpers.SelfCheckError("forced"),
        )
        with pytest.raises(SystemExit) as exc:
            _run_command(tmp_path)
        assert exc.value.code == 2


# ---- Sanitised exception bubbling (FR-APPLY-LOG1) ------------------------

class TestSanitisedExceptions:

    @pytest.mark.django_db
    def test_admin_create_failure_does_not_leak_password(
        self, tmp_path, capfd, mocker,
    ):
        _write_pending(
            tmp_path, username="grace", password=STRONG_PASSWORD,
        )
        # Make the create_superuser path raise a generic exception
        # whose message would normally include the password.
        mocker.patch.object(
            User.objects, "create_superuser",
            side_effect=RuntimeError(f"db error near {STRONG_PASSWORD}"),
        )
        with pytest.raises(SystemExit) as exc:
            _run_command(tmp_path)
        assert exc.value.code == 2
        captured = capfd.readouterr()
        # Sanitised stderr must NOT include the password.
        assert STRONG_PASSWORD not in captured.err
        assert STRONG_PASSWORD not in captured.out

    @pytest.mark.django_db
    def test_password_not_in_caplog(self, tmp_path, caplog):
        _write_pending(
            tmp_path, username="harry", password=STRONG_PASSWORD,
        )
        with caplog.at_level(logging.DEBUG):
            _run_command(tmp_path)
        for record in caplog.records:
            assert STRONG_PASSWORD not in record.getMessage()


# ---- Helpers — direct unit tests -----------------------------------------

class TestHelpers:

    def test_is_pending_stale_no_timestamp(self):
        assert helpers.is_pending_stale({}) is True

    def test_is_pending_stale_recent(self):
        assert helpers.is_pending_stale(
            {"created_at_unix": time.time()},
        ) is False

    def test_is_pending_stale_ancient(self):
        assert helpers.is_pending_stale(
            {"created_at_unix": time.time() - (48 * 3600)},
        ) is True

    def test_schema_version_supported(self):
        assert helpers.schema_version_supported(
            {"schema_version": 1},
        ) is True
        assert helpers.schema_version_supported(
            {"schema_version": 99},
        ) is False
        assert helpers.schema_version_supported({}) is False

    def test_acquire_apply_lock_handle(self, tmp_path):
        h = helpers.acquire_apply_lock(tmp_path)
        assert h is not None
        # Holding handle, second attempt must return None.
        h2 = helpers.acquire_apply_lock(tmp_path)
        assert h2 is None
        # Release: close fd so a re-acquire works.
        os.close(h.fd)

    def test_best_effort_unlink_missing_no_raise(self, tmp_path):
        helpers.best_effort_unlink(tmp_path / "ghost")

    def test_read_pending_setup_missing_raises_guard(self, tmp_path):
        with pytest.raises(helpers.PendingSetupGuardError):
            helpers.read_pending_setup(tmp_path / "absent.json")

    def test_read_pending_setup_malformed_raises_guard(self, tmp_path):
        target = tmp_path / "pending_setup.json"
        target.write_text("not json", encoding="utf-8")
        with pytest.raises(helpers.PendingSetupGuardError):
            helpers.read_pending_setup(target)


# ---- emit_stderr_and_exit ------------------------------------------------

class TestEmitStderrAndExit:

    def test_writes_message_and_exits(self, capfd):
        with pytest.raises(_ExitCalled) as exc:
            helpers.emit_stderr_and_exit("hello\n", 2)
        assert exc.value.code == 2
        captured = capfd.readouterr()
        assert "hello" in captured.err

    def test_appends_newline(self, capfd):
        with pytest.raises(_ExitCalled):
            helpers.emit_stderr_and_exit("no-trailing", 1)
        captured = capfd.readouterr()
        assert captured.err.endswith("\n")
