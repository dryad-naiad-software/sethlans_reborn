# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Pre-apply guards for the ``apply_pending_setup`` Django management
command.

Covers FR-APPLY-INVOKE (argparse allowlist), FR-APPLY1a (lock
contention), FR-APPLY1b (schema-version mismatch), and FR-APPLY1c
(pending-setup TTL gate). Behavioral tests for the apply lifecycle
itself live in ``test_apply_pending_setup_lifecycle.py``; invariants
(self-check, sanitised exceptions, helpers) in
``test_apply_pending_setup_invariants.py``.
"""

from __future__ import annotations

import time

import pytest

from workers.management.commands import (
    apply_pending_setup as apply_cmd,
)

from ._apply_pending_setup_helpers import (
    _ExitCalled,
    _run_command,
    _write_pending,
)


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
