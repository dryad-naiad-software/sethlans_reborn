# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Apply lifecycle for the ``apply_pending_setup`` Django management
command.

Covers FR-APPLY2 (full apply sequence — admin user + sentinel +
pending-file unlink + enrollment-key persistence) and FR-APPLY5
(idempotent re-run, crash recovery from a pre-existing sentinel).
Pre-apply guards live in ``test_apply_pending_setup_gates.py``;
invariants (self-check, sanitised exceptions, helpers) in
``test_apply_pending_setup_invariants.py``.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from workers.management.commands import (
    apply_pending_setup as apply_cmd,
    apply_pending_setup_helpers as helpers,
)

from ._apply_pending_setup_helpers import (
    _ExitCalled,
    _run_command,
    _write_pending,
)


User = get_user_model()


@pytest.fixture(autouse=True)
def _patch_os_exit(monkeypatch):
    """Convert ``os._exit`` into a ``SystemExit`` so pytest can catch it."""
    def fake_exit(code):
        raise _ExitCalled(code)
    monkeypatch.setattr(
        "workers.management.commands.apply_pending_setup_helpers.os._exit",
        fake_exit,
    )


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
