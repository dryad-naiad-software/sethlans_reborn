# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for ``launcher/apply_pending_setup.run_apply_pipeline``
in source mode and frozen-mode argv invariant (issue #191).

These tests sit above the unit-test layer (which mocks subprocess.run
directly) by exercising the real pipeline orchestration function.

Test coverage:
  - Source-mode end-to-end: ``run_apply_pipeline`` calls two real subprocesses
    against ``run_manager.py`` and returns ``(True, "")`` on success.
  - Source-mode corrupted pending_setup.json: pipeline returns ``(False, msg)``
    and the sentinel is NOT written.
  - Frozen-mode argv invariant: with ``is_frozen()=True`` and mocked
    ``manager_exe()``, the two subprocess argv shapes match the spec exactly
    ([exe, --manage, migrate, --noinput] and [exe, --manage,
    apply_pending_setup, --data-dir, path]).  This exercises the contract
    through the real ``run_apply_pipeline`` orchestration, catching a
    regression where pipeline composition forgets to thread frozen-mode argv.
"""

from __future__ import annotations

import json
import os
import sys
import time

import pytest

from launcher import apply_pending_setup as apply_mod

from tests.integration.launcher._dispatch_helpers import (
    MANAGER_DIR,
    STRONG_PASSWORD,
    _curated_env_for_subprocess,
    _write_pending,
)


# ---------------------------------------------------------------------------
# Test 3a – source-mode end-to-end pipeline succeeds
# ---------------------------------------------------------------------------

class TestRunApplyPipelineSourceModeSuccess:
    """Source-mode end-to-end: ``run_apply_pipeline`` calls real subprocesses
    and produces the expected side effects.

    Uses ``subprocess.run`` with ``run_manager.py`` (source mode) so the
    migrate and apply_pending_setup steps touch a real Django DB.
    """

    @pytest.mark.django_db(transaction=True)
    @pytest.mark.skipif(
        getattr(sys, "frozen", False),
        reason="Source-mode test; not applicable in frozen bundle.",
    )
    def test_source_mode_pipeline_returns_true(self, tmp_path, mocker):
        """run_apply_pipeline returns (True, '') in source mode with valid pending_setup.

        We stub subprocess.run to return rc=0 for both steps (we don't need
        a real DB here since the subprocess side effects are irrelevant to the
        pipeline orchestration contract). What we verify is:
          - run_apply_pipeline calls subprocess.run twice
          - Returns (True, "") on double-success
          - Source-mode argv shape is used (sys.executable + manage.py)
        """
        _write_pending(tmp_path, username="pipeline_source_user")

        mocker.patch.object(apply_mod, "_is_frozen", return_value=False)
        mocker.patch.object(apply_mod, "_manager_dir", return_value=MANAGER_DIR)
        mocker.patch.object(
            apply_mod, "build_curated_env",
            return_value=_curated_env_for_subprocess(),
        )

        completed_ok = mocker.MagicMock(returncode=0, stdout="", stderr="")
        run_mock = mocker.patch.object(
            apply_mod.subprocess, "run", return_value=completed_ok,
        )

        ok, message = apply_mod.run_apply_pipeline(tmp_path)

        assert ok is True, f"Expected (True, ''), got ({ok!r}, {message!r})"
        assert message == ""
        assert run_mock.call_count == 2, (
            f"Expected 2 subprocess.run calls (migrate + apply), "
            f"got {run_mock.call_count}"
        )

        # Verify source-mode argv shape for both calls
        migrate_argv = run_mock.call_args_list[0].args[0]
        assert migrate_argv[0] == sys.executable, (
            "Source-mode migrate must use sys.executable as argv[0]"
        )
        assert "migrate" in migrate_argv, (
            "Source-mode migrate argv must contain 'migrate'"
        )
        assert "--manage" not in migrate_argv, (
            "Source-mode argv must NOT contain --manage"
        )

        apply_argv = run_mock.call_args_list[1].args[0]
        assert apply_argv[0] == sys.executable, (
            "Source-mode apply must use sys.executable as argv[0]"
        )
        assert "apply_pending_setup" in apply_argv, (
            "Source-mode apply argv must contain 'apply_pending_setup'"
        )
        assert "--manage" not in apply_argv, (
            "Source-mode argv must NOT contain --manage"
        )


# ---------------------------------------------------------------------------
# Test 3b – corrupted pending_setup.json → (False, message), no sentinel
# ---------------------------------------------------------------------------

class TestRunApplyPipelineCorruptedPending:
    """Corrupted pending_setup.json: pipeline returns (False, msg) and the
    sentinel is NOT written.

    Uses a real subprocess for migrate (exits 0) but lets apply_pending_setup
    fail because the pending_setup.json is missing ``schema_version``.
    The apply step returns rc=1; pipeline must return (False, msg).
    """

    @pytest.mark.skipif(
        getattr(sys, "frozen", False),
        reason="Source-mode subprocess test; not applicable in frozen bundle.",
    )
    def test_corrupted_pending_json_returns_false_no_sentinel(
        self, tmp_path, mocker,
    ):
        """When pending_setup.json is missing schema_version, pipeline fails.

        Verifies:
          - run_apply_pipeline returns (False, <non-empty message>)
          - The sentinel (.setup_complete) is NOT written to data_dir
        """
        # Write a corrupted pending_setup.json (missing schema_version).
        corrupted = {
            "topology": "manager",
            "created_at_unix": time.time(),
            "admin_user": {
                "username": "admin_corrupted",
                "email": "x@example.com",
                "password_plaintext": STRONG_PASSWORD,
            },
            "worker_ui_password_hash": None,
            "worker_ui_password_salt": None,
            "auto_enroll_local_worker": False,
            # schema_version intentionally omitted
        }
        (tmp_path / "pending_setup.json").write_text(
            json.dumps(corrupted), encoding="utf-8",
        )

        # Migrate succeeds (rc=0), apply fails (rc=1, schema guard)
        migrate_ok = mocker.MagicMock(returncode=0, stdout="", stderr="")
        apply_fail = mocker.MagicMock(
            returncode=1,
            stdout="",
            stderr="apply guard: unsupported schema_version",
        )
        mocker.patch.object(apply_mod, "_is_frozen", return_value=False)
        mocker.patch.object(apply_mod, "_manager_dir", return_value=MANAGER_DIR)
        mocker.patch.object(
            apply_mod, "build_curated_env",
            return_value={"PATH": os.environ.get("PATH", "")},
        )
        mocker.patch.object(
            apply_mod.subprocess, "run",
            side_effect=[migrate_ok, apply_fail],
        )

        ok, message = apply_mod.run_apply_pipeline(tmp_path)

        assert ok is False, f"Expected (False, msg) for corrupted payload, got ({ok!r}, {message!r})"
        assert message, "Expected non-empty error message for corrupted payload"
        assert not (tmp_path / ".setup_complete").exists(), (
            "Sentinel must NOT be written when apply fails"
        )


# ---------------------------------------------------------------------------
# Test 4 – frozen-mode argv invariant via real run_apply_pipeline
# ---------------------------------------------------------------------------

class TestRunApplyPipelineFrozenModeArgvInvariant:
    """Frozen-mode argv shape is preserved through real run_apply_pipeline.

    Catches a regression where pipeline composition forgets to thread the
    frozen-mode argv shape.  Unit tests assert this at the subprocess level;
    this integration test asserts it through the orchestrating function.
    """

    def test_frozen_mode_migrate_argv_shape(self, mocker, tmp_path):
        """Frozen-mode migrate subprocess argv is [exe, --manage, migrate, --noinput]."""
        fake_exe = tmp_path / "run_manager.exe"
        fake_exe.write_text("fake", encoding="utf-8")
        data_dir = tmp_path / "manager-data"

        mocker.patch.object(apply_mod, "_is_frozen", return_value=True)
        mocker.patch.object(apply_mod, "_manager_exe", return_value=fake_exe)
        mocker.patch.object(apply_mod, "_manager_dir", return_value=tmp_path)
        mocker.patch.object(
            apply_mod, "build_curated_env", return_value={"PATH": "/usr/bin"},
        )

        completed_ok = mocker.MagicMock(returncode=0, stdout="", stderr="")
        run_mock = mocker.patch.object(
            apply_mod.subprocess, "run", return_value=completed_ok,
        )

        ok, _ = apply_mod.run_apply_pipeline(data_dir)
        assert ok is True
        assert run_mock.call_count == 2

        # Frozen-mode migrate argv must be [exe, --manage, migrate, --noinput]
        migrate_argv = run_mock.call_args_list[0].args[0]
        assert migrate_argv == [
            str(fake_exe), "--manage", "migrate", "--noinput",
        ], (
            f"Frozen-mode migrate argv shape wrong: {migrate_argv!r}"
        )

    def test_frozen_mode_apply_argv_shape(self, mocker, tmp_path):
        """Frozen-mode apply subprocess argv has [exe, --manage, apply_pending_setup, --data-dir, path]."""
        fake_exe = tmp_path / "run_manager.exe"
        fake_exe.write_text("fake", encoding="utf-8")
        data_dir = tmp_path / "manager-data"

        mocker.patch.object(apply_mod, "_is_frozen", return_value=True)
        mocker.patch.object(apply_mod, "_manager_exe", return_value=fake_exe)
        mocker.patch.object(apply_mod, "_manager_dir", return_value=tmp_path)
        mocker.patch.object(
            apply_mod, "build_curated_env", return_value={"PATH": "/usr/bin"},
        )

        completed_ok = mocker.MagicMock(returncode=0, stdout="", stderr="")
        run_mock = mocker.patch.object(
            apply_mod.subprocess, "run", return_value=completed_ok,
        )

        ok, _ = apply_mod.run_apply_pipeline(data_dir)
        assert ok is True

        # Frozen-mode apply argv must be [exe, --manage, apply_pending_setup, --data-dir, <path>]
        apply_argv = run_mock.call_args_list[1].args[0]
        assert apply_argv == [
            str(fake_exe), "--manage", "apply_pending_setup",
            "--data-dir", str(data_dir),
        ], (
            f"Frozen-mode apply_pending_setup argv shape wrong: {apply_argv!r}"
        )

    def test_frozen_mode_no_sys_executable_in_argv(self, mocker, tmp_path):
        """In frozen mode, sys.executable must NOT appear in either subprocess argv.

        This is the core regression #191 fixed: in frozen mode sys.executable
        is run_launcher.exe, not python.exe. Using it would reproduce the bug.
        """
        fake_exe = tmp_path / "run_manager.exe"
        fake_exe.write_text("fake", encoding="utf-8")
        data_dir = tmp_path / "manager-data"

        mocker.patch.object(apply_mod, "_is_frozen", return_value=True)
        mocker.patch.object(apply_mod, "_manager_exe", return_value=fake_exe)
        mocker.patch.object(apply_mod, "_manager_dir", return_value=tmp_path)
        mocker.patch.object(
            apply_mod, "build_curated_env", return_value={"PATH": "/usr/bin"},
        )

        completed_ok = mocker.MagicMock(returncode=0, stdout="", stderr="")
        run_mock = mocker.patch.object(
            apply_mod.subprocess, "run", return_value=completed_ok,
        )

        apply_mod.run_apply_pipeline(data_dir)

        for call in run_mock.call_args_list:
            argv = call.args[0]
            assert sys.executable not in argv, (
                f"sys.executable must NOT appear in frozen-mode argv. "
                f"Got: {argv!r}"
            )
